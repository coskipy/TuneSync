document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();
		const url = document.getElementById("url").value;
		const fileInput = document.getElementById("path");
		const statusDiv = document.getElementById("status");

		// Get the selected file(s)
		const file = fileInput.files[0]; // For single file selection

		if (!file) {
			statusDiv.textContent = "No file selected.";
			return;
		}

		statusDiv.textContent = "Downloading...";

		const response = await fetch("/download", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ url }),
		});

		const result = await response.json();
		if (response.ok) {
			statusDiv.textContent = result.message;
		} else {
			statusDiv.textContent = `Error: ${result.message}`;
			statusDiv.style.color = "red";
		}
	});

document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();

		const filePath = document.getElementById("file-path").value;
		const statusDiv = document.getElementById("status");
		statusDiv.textContent = `Sending path: ${filePath}...`;

		// Send the file path to the server
		const response = await fetch("/file-path", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ filePath }),
		});

		const result = await response.json();
		if (response.ok) {
			statusDiv.textContent = `Path received successfully: ${result.message}`;
		} else {
			statusDiv.textContent = `Error: ${result.message}`;
			statusDiv.style.color = "red";
		}
	});
