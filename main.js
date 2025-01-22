const { app, BrowserWindow } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

// Start Flask server
const startFlaskServer = () => {
	const flaskProcess = spawn("python3", [path.join(__dirname, "app.py")]);
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

let mainWindow;

function createWindow() {
	let mainWindow = new BrowserWindow({
		width: 800,
		height: 600,
		webPreferences: {
			// preload: path.join(__dirname, "preload.js"),
			nodeIntegration: true, // Allow Node.js integration
			contextIsolation: false, // Disable context isolation for testing
		},
	});

	mainWindow.loadURL("http://127.0.0.1:5000"); // Load Flask's local URL

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
		flaskProcess.kill();
	}
});
