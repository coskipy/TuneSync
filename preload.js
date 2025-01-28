const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
	selectFolder: () => ipcRenderer.invoke("select-folder"),
	selectFolders: () => ipcRenderer.invoke("select-folders"),
});
