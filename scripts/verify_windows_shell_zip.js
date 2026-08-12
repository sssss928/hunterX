// Verify a ZIP through the same Windows Shell namespace used by File Explorer.
(function () {
    if (WScript.Arguments.length !== 1) {
        WScript.Echo("usage: cscript //nologo verify_windows_shell_zip.js <archive.zip>");
        WScript.Quit(2);
    }

    var fileSystem = new ActiveXObject("Scripting.FileSystemObject");
    var archivePath = fileSystem.GetAbsolutePathName(WScript.Arguments.Item(0));
    if (!fileSystem.FileExists(archivePath)) {
        WScript.Echo("Windows Shell ZIP verification failed: archive is missing");
        WScript.Quit(1);
    }

    var shell = new ActiveXObject("Shell.Application");
    var root = shell.NameSpace(archivePath);
    if (root === null) {
        WScript.Echo("Windows Shell ZIP verification failed: namespace unavailable");
        WScript.Quit(1);
    }

    var rootItems = root.Items();
    if (rootItems.Count === 0) {
        WScript.Echo("Windows Shell ZIP verification failed: archive appears empty");
        WScript.Quit(1);
    }

    var names = [];
    for (var index = 0; index < rootItems.Count; index += 1) {
        names.push(rootItems.Item(index).Name);
    }
    names.sort();
    WScript.Echo(
        '{"archive":"' + archivePath.replace(/\\/g, "\\\\") +
        '","root_items":' + rootItems.Count +
        ',"names":"' + names.join("|").replace(/"/g, "\\\"") + '"}'
    );
    WScript.Quit(0);
}());
