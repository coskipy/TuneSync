const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electron", {
	// Add methods here to interact with your Flask backend
});
