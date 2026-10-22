/*
 * EffectCraft 0.13.0 — безопасный учебный пример для run_script / script.run.
 * Официальная объектная модель и времена:
 * https://github.com/storytold/effectcraft/blob/de3fec28ed7a3d7a700a9d8ea307779dcda99a8e/docs/agents.md
 *
 * Добавляет композицию в текущий проект. Не вызывает new/open/save/quit,
 * не читает внешние файлы, не зависит от абсолютных путей и шрифтов извне.
 * Повторный запуск создаёт ещё одну композицию; это намеренно не идемпотентно.
 * Undo group объединяет правки, но при исключении не откатывает их сама.
 * Итоговое значение сценария — JSON-строка с id созданной композиции.
 */
(function () {
    app.beginUndoGroup("Codex bridge: title demo");
    try {
        // addComp(name, width, height, pixelAspect, durationSeconds, fps).
        var comp = app.project.items.addComp("Codex Bridge Demo", 1280, 720, 1, 4, 30);
        var background = comp.layers.addSolid([0.055, 0.055, 0.065], "Background", 1280, 720, 1, 4);
        var accent = comp.layers.addSolid([0.88, 1.0, 0.22], "Accent line", 1024, 12, 1, 4);
        accent.property("ADBE Transform Group").property("ADBE Position").setValue([640, 510]);

        var title = comp.layers.addText("CODEX + EFFECTCRAFT");
        title.name = "Bridge title";
        // TextDocument изменяют копированием value и обратной записью setValue.
        var textProperty = title.property("ADBE Text Properties").property("ADBE Text Document");
        var textDocument = textProperty.value;
        textDocument.fontSize = 68;
        textDocument.fillColor = [0.96, 0.96, 0.93];
        textProperty.setValue(textDocument);

        var transform = title.property("ADBE Transform Group");
        var position = transform.property("ADBE Position");
        // Scripting setValueAtTime принимает секунды композиции.
        // В engine prop.addKey время по умолчанию слоя: задавайте timeBase:"comp".
        position.setValueAtTime(0, [640, 460]);
        position.setValueAtTime(1.2, [640, 350]);
        position.setTemporalEaseAtKey(1, [new KeyframeEase(0, 60)], [new KeyframeEase(0, 60)]);
        position.setTemporalEaseAtKey(2, [new KeyframeEase(0, 60)], [new KeyframeEase(0, 60)]);

        var opacity = transform.property("ADBE Opacity");
        opacity.setValueAtTime(0, 0);
        opacity.setValueAtTime(0.8, 100);
        opacity.setValueAtTime(3.2, 100);
        opacity.setValueAtTime(3.95, 0);

        var subtitle = comp.layers.addText("LOCAL API / NATIVE MCP / 0.13.0");
        subtitle.name = "Bridge subtitle";
        var subtitleProperty = subtitle.property("ADBE Text Properties").property("ADBE Text Document");
        var subtitleDocument = subtitleProperty.value;
        subtitleDocument.fontSize = 30;
        subtitleDocument.fillColor = [0.88, 1.0, 0.22];
        subtitleProperty.setValue(subtitleDocument);
        subtitle.property("ADBE Transform Group").property("ADBE Position").setValue([640, 445]);

        // id используйте в render_frame / save_project, не угадывайте активную comp.
        return JSON.stringify({comp: comp.id, name: comp.name, width: 1280, height: 720, duration: 4, frameRate: 30});
    } finally {
        app.endUndoGroup();
    }
})();
