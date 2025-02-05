const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
	selectFolder: () => ipcRenderer.invoke("select-folder"),
	selectFolders: () => ipcRenderer.invoke("select-folders"),
	startWatching: (dirPath) => ipcRenderer.send("start-watching", dirPath),
	stopWatching: () => ipcRenderer.send("stop-watching"),
	onFileListInitial: (callback) =>
		ipcRenderer.on("file-list-initial", (event, files) => callback(files)),
	onFileAdded: (callback) =>
		ipcRenderer.on("file-added", (event, file) => callback(file)),
	onFileRemoved: (callback) =>
		ipcRenderer.on("file-removed", (event, file) => callback(file)),
});
