let absoluteDirPath = "";

document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();
		const url = document.getElementById("url").value;
		const statusDiv = document.getElementById("status");

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

async function selectFolderAndUpdateUI(buttonId, outputId) {
	const folderPath = await window.electron.selectFolder();
	const button = document.getElementById(buttonId);
	const outputElement = document.getElementById(outputId);

	if (folderPath) {
		button.textContent = `Selected Folder: ${folderPath}`;
	} else {
		button.textContent = "No folder selected.";
	}

	// You can return the folder path if needed
	return folderPath;
}

// document.getElementById("path").addEventListener("change", function (event) {
// 	const files = event.target.files;
// 	if (files.length > 0) {
// 		// Get the webkitRelativePath of the first file to extract the folder name
// 		const folderPath = files[0].webkitRelativePath; // Get path like 'folder_name/file1.txt'
// 		const folderName = folderPath.split("/")[0]; // Split at '/' and take the first part

// 		document.getElementById("choose-file").textContent = folderName;
// 	} else {
// 		console.log("No folder selected.");
// 	}
// });
