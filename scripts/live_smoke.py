#!/usr/bin/env python3
"""Opt-in real GUI/API/MCP check. Adds a new comp, never opens/replaces a project."""
from pathlib import Path
import argparse
import base64
import importlib.util
import json
import os
import queue
import struct
import subprocess
import sys
import threading
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from effectcraft_codex.transport import ControlClient

spec = importlib.util.spec_from_file_location('example_client', ROOT / 'examples/api_client.py')
api_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_module)

def png_dimensions(data):
    if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[8:16] != b'\x00\x00\x00\rIHDR':
        raise ValueError('Expected a PNG with a valid IHDR header')
    width, height = struct.unpack('>II', data[16:24])
    if not width or not height:
        raise ValueError('PNG dimensions must be positive')
    return width, height

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, default=ROOT / '.state/api-token')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    client = api_module.Client(args.token_file)
    before = client.request('GET', '/v1/state')
    capabilities = client.request('POST', '/v1/command', {'command':'app.capabilities'})
    assert capabilities['version'] == '0.13.0'
    commands = client.request('GET', '/v1/commands?filter=comp.new')
    assert any(c['id'] == 'comp.new' for c in commands)
    name = 'Codex API + MCP ' + datetime.now(timezone.utc).strftime('%H%M%S')
    def command(command, params):
        return client.request('POST','/v1/command',{'command':command,'params':params})
    comp = command('comp.new',{'name':name,'width':960,'height':540,'frameRate':30,'duration':3})
    command('layer.newSolid',{'name':'Background','color':'#151515','width':960,'height':540})
    layer = command('layer.newText',{'name':'Connected','text':'CODEX + EFFECTCRAFT','font':'Inter','size':56,'fill':'#e7ff51','position':[480,270]})['layer']
    command('prop.addKey',{'layer':layer,'path':'transform/opacity','time':0,'timeBase':'comp','value':0})
    command('prop.addKey',{'layer':layer,'path':'transform/opacity','time':1,'timeBase':'comp','value':100})
    command('time.set',{'time':1.5})
    frame = client.request('POST','/v1/render-frame',{'comp':name,'time':1.5,'max_side':960})
    png = base64.b64decode(frame['png'],validate=True)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    (args.out/'api-preview.png').write_bytes(png)
    command('file.saveCopy',{'path':str((args.out/'connected.ecproj').resolve())})
    mcp = subprocess.Popen([sys.executable,str(ROOT/'bridge.py'),'mcp','--no-launch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    replies=queue.Queue()
    def read():
        for line in mcp.stdout:
            replies.put(json.loads(line))
    threading.Thread(target=read,daemon=True).start()
    sequence=0
    def rpc(method,params):
        nonlocal sequence
        sequence+=1
        mcp.stdin.write(json.dumps({'jsonrpc':'2.0','id':sequence,'method':method,'params':params})+'\n');mcp.stdin.flush()
        while True:
            response=replies.get(timeout=70)
            if response.get('id')==sequence:
                assert 'error' not in response,response
                return response['result']
    try:
        initialize=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'bridge-live-smoke','version':'1.0.0'}})
        mcp.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');mcp.stdin.flush()
        tools=rpc('tools/list',{})['tools']
        names={t['name'] for t in tools}
        assert {'get_state','get_comp','render_frame','ui_inspect','screenshot'} <= names
        state=rpc('tools/call',{'name':'get_state','arguments':{}})
        assert not state.get('isError'),state
        current=rpc('tools/call',{'name':'get_comp','arguments':{'comp':name}})
        assert not current.get('isError') and name in json.dumps(current,ensure_ascii=False),current
        rendered=rpc('tools/call',{'name':'render_frame','arguments':{'comp':name,'time':1.5,'max_side':960,'inline':True}})
        assert not rendered.get('isError'),rendered
        images=[part for part in rendered['content'] if part.get('type')=='image']
        assert images,'Native MCP must return a real image'
        mcp_png=base64.b64decode(images[0]['data'],validate=True)
        (args.out/'mcp-preview.png').write_bytes(mcp_png)
        # API may encode RGBA while native MCP re-encodes RGB. Encoded bytes
        # can differ for identical pixels; this stdlib smoke check compares IHDR.
        dimensions=png_dimensions(png)
        assert png_dimensions(mcp_png)==dimensions,'API and MCP frame dimensions must agree'
        assert dimensions==(frame['width'],frame['height']),'PNG and API metadata must agree'
        report={'version':capabilities['version'],'commands':capabilities['commands'],'effects':capabilities['effects']['total'],'api':{'health':True,'schemaDiscovery':True,'composition':comp,'width':frame['width'],'height':frame['height'],'pngBytes':len(png)},'mcp':{'serverInfo':initialize['serverInfo'],'toolCount':len(tools),'sameLiveProject':True,'pngDimensionsMatch':True},'projectCopy':str((args.out/'connected.ecproj').resolve()),'priorActiveComp':before.get('active_comp'),'retries':0}
        (args.out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        mcp.stdin.close()
        try:mcp.wait(timeout=5)
        except subprocess.TimeoutExpired:mcp.terminate();mcp.wait(timeout=5)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
