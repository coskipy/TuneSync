const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");

let flaskProcess;
let mainWindow;

const FLASK_URL = "http://127.0.0.1:5000";

// Start Flask server
const startFlaskServer = () => {
	flaskProcess = spawn("python3", [path.join(__dirname, "app.py")]);

	flaskProcess.stdout.on("data", (data) => {
		console.log(`Flask stdout: ${data}`);
	});

	flaskProcess.stderr.on("data", (data) => {
		console.error(`Flask stderr: ${data}`);
	});

	flaskProcess.on("close", (code) => {
		console.log(`Flask process exited with code ${code}`);
	});

	flaskProcess.on("error", (err) => {
		console.error("Error starting Flask server:", err);
	});
};

// Check if Flask server is ready
const waitForFlaskServer = async () => {
	const MAX_RETRIES = 50; // Number of attempts before giving up
	const RETRY_DELAY = 100; // Delay between retries in milliseconds

	let retries = 0;
	while (retries < MAX_RETRIES) {
		try {
			await new Promise((resolve, reject) => {
				const req = http.get(FLASK_URL, (res) => {
					if (res.statusCode === 200) {
						resolve();
					} else {
						reject();
					}
				});

				req.on("error", reject);
				req.end();
			});

			console.log("Flask server is ready!");
			return true;
		} catch (err) {
			retries++;
			console.log(
				`Waiting for Flask server... (${retries}/${MAX_RETRIES})`
			);
			await new Promise((r) => setTimeout(r, RETRY_DELAY));
		}
	}

	console.error("Flask server did not start in time.");
	return false;
};

// Create the Electron window
async function createWindow() {
	const serverReady = await waitForFlaskServer();

	if (!serverReady) {
		console.error("Failed to connect to the Flask server. Exiting.");
		app.quit();
		return;
	}

	mainWindow = new BrowserWindow({
		width: 375,
		height: 530,
		webPreferences: {
			preload: path.join(__dirname, "preload.js"), // Preload script
			contextIsolation: true, // Enable context isolation for security
			nodeIntegration: false, // Disable nodeIntegration in the renderer process
		},
	});

	console.log("Loading Flask URL...");
	mainWindow.loadURL(FLASK_URL);

	mainWindow.on("closed", () => {
		mainWindow = null;
	});
}

// When Electron is ready, start Flask and create the Electron window
app.whenReady().then(() => {
	startFlaskServer();
	createWindow();

	app.on("activate", () => {
		if (BrowserWindow.getAllWindows().length === 0) {
			createWindow();
		}
	});
});

app.on("window-all-closed", () => {
	if (process.platform !== "darwin") {
		app.quit();
	}
	if (flaskProcess) {
		flaskProcess.kill("SIGTERM"); // Graceful termination
	}
});

// Foler creation and selection for download
ipcMain.handle("select-folder", async () => {
	const result = await dialog.showOpenDialog({
		properties: ["openDirectory", "createDirectory"],
	});

	if (!result.canceled && result.filePaths.length > 0) {
		return result.filePaths[0]; // Return the selected directory path
	}

	return null;
});

// Multi-folder selection for sync selected
ipcMain.handle("select-folders", async () => {
	const result = await dialog.showOpenDialog({
		properties: ["openDirectory", "multiSelections"],
	});

	if (!result.canceled && result.filePaths.length > 0) {
		return result.filePaths; // Return the selected directory paths
	}

	return null;
});
