const statusDiv = document.getElementById("status");

// Universal SSE handler
function createSSEConnection(url, data, action) {
	statusDiv.textContent = `${action} started...`;
	console.log(`${action} initiated`);

	// Convert POST data to query params for SSE
	const params = new URLSearchParams(data);
	const eventSource = new EventSource(`${url}?${params}`);

	eventSource.onmessage = (event) => {
		const message = event.data.trim();
		console.log(`${action} Update:`, message);

		// Special completion marker
		if (message === "SYNC_COMPLETE") {
			statusDiv.textContent += "\nOperation completed successfully";
			eventSource.close();
		} else {
			statusDiv.textContent += `\n${message}`;
		}
	};

	eventSource.onerror = (error) => {
		console.error(`${action} Error:`, error);
		statusDiv.textContent += `\n${action} failed - see console`;
		eventSource.close();
	};
}

// Download form handler
document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();
		const url = document.getElementById("url").value;
		const folderPath = await window.electron.selectFolder();

		createSSEConnection(
			"/download",
			{
				url: url,
				path: folderPath,
			},
			"Download"
		);
	});

// Sync All handler
document.getElementById("sync-all").addEventListener("click", () => {
	createSSEConnection("/sync-all", {}, "Full Sync");
});

// Sync Selected handler
document.getElementById("sync-selected").addEventListener("click", async () => {
	const folderPaths = await window.electron.selectFolders();

	if (!folderPaths?.length) {
		statusDiv.textContent = "No folders selected";
		return;
	}

	createSSEConnection(
		"/sync-selected",
		{
			paths: JSON.stringify(folderPaths),
		},
		"Selected Sync"
	);
});
