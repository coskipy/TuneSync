const statusDiv = document.getElementById("status");

document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();
		const url = document.getElementById("url").value;

		const folderPath = await window.electron.selectFolder();

		const data = {
			url: url,
			path: folderPath,
		};

		statusDiv.textContent = "Downloading...";

		const response = await fetch("/download", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(data),
		});

		const result = await response.json();
		if (response.ok) {
			statusDiv.textContent = result.message;
		} else {
			statusDiv.textContent = `Error: ${result.message}`;
			statusDiv.style.color = "red";
		}
	});

document.getElementById("sync-all").addEventListener("click", async () => {
	statusDiv.textContent = "Syncing...";

	const response = await fetch("/sync-all", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
	});

	const result = await response.json();
	if (response.ok) {
		statusDiv.textContent = result.message;
	} else {
		statusDiv.textContent = `Error: ${result.message}`;
		statusDiv.style.color = "red";
	}
});

document.getElementById("sync-selected").addEventListener("click", async () => {
	statusDiv.textContent = "Syncing selected playlists...";

	const folderPaths = await window.electron.selectFolders();
	console.log(folderPaths);

	const data = {
		paths: folderPaths,
	};

	if (!folderPaths || folderPaths.length === 0) {
		statusDiv.textContent = "No folders selected.";
		return;
	} else {
		statusDiv.textContent = "Syncing " + folderPaths.length + " folders...";
	}

	const response = await fetch("/sync-selected", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(data),
	});

	const result = await response.json();
	if (response.ok) {
		statusDiv.textContent = result.message;
	} else {
		statusDiv.textContent = `Error: ${result.message}`;
		statusDiv.style.color = "red";
	}
});
