const statusDiv = document.getElementById("status");
let metadataMap = null;
let downloadType = "download";

document.getElementById("url").addEventListener("input", function () {
	if (this.validity.typeMismatch) {
		this.setCustomValidity('"open.spotify.com" URL is required');
	} else {
		this.setCustomValidity(""); // Clears the error when valid
	}
});

document
	.getElementById("download-form")
	.addEventListener("submit", async (e) => {
		e.preventDefault();

		downloadType = "download";

		// Update UI
		statusDiv.style.color = "green";
		statusDiv.textContent = "";

		const url = document.getElementById("url").value;
		const folderPath = await window.electron.selectFolder();

		if (!folderPath) {
			canceldownload();
			return;
		}

		document.getElementById("go-button").style.display = "none";
		document.getElementById("cancel-button").style.display = "flex";
		document.getElementById("progress").style.display = "flex";

		document.getElementById("progress-title").textContent =
			"Fetching metadata...";
		statusDiv.textContent = "Fetching metadata...";

		// Retrieve metadata
		const data = { url: url, path: folderPath };
		const metadataResponse = await fetch("/get-metadata", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(data),
		});

		const metadataResult = await metadataResponse.json();
		if (metadataResponse.ok) {
			document.getElementById("progress-count-songs").style.display =
				"flex";
			setSongsExpectedText(metadataResult.songs.length);
		} else {
			statusDiv.textContent = `Error: ${metadataResult.message}`;
			statusDiv.style.color = "red";
		}

		// updateProgressListUI(metadataResult, "initial");

		document.getElementById("progress-title").textContent =
			"Downloading...";
		statusDiv.textContent = "Downloading...";

		// Start watching target folder
		window.electron.startWatching(folderPath);

		metadataMap = new Map(
			metadataResult.songs.map((song) => [
				`${song.artist} - ${song.name}`, // Key: "artist - song name"
				song, // Value: The whole song object
			])
		);

		console.log(metadataMap); // Check the map

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

		document.getElementById("progress-title").textContent =
			"Download complete!";

		document.getElementById("loading-bar-container").style.display = "none";

		// Stop watching target folder
		window.electron.stopWatching();
	});

document.getElementById("sync-all").addEventListener("click", async () => {
	downloadType = "sync";
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

document.getElementById("progress-header").addEventListener("click", () => {
	// If expanded
	if (document.getElementById("progress-show").style.display === "none") {
		document.getElementById("progress-show").style.display = "flex";
		document.getElementById("progress-hide").style.display = "none";
		document.getElementById("progress-window").style.display = "flex";
	}
	// If collapsed
	else {
		document.getElementById("progress-show").style.display = "none";
		document.getElementById("progress-hide").style.display = "flex";
		document.getElementById("progress-window").style.display = "none";
	}
});

function setSongsExpectedText(number) {
	document.getElementById("number-expected").textContent = number;
}

function stripFileExtension(filename) {
	// Only remove extension if the period is at the end of the string
	return filename.replace(/(?<=^.*)(?=\.[^/.]+$)/, "");
}

// function modifyDownloadProgressList(song, action) {
// // Strip the file extension from the song name
// const songWithoutExtension = stripFileExtension(song);

// // Retrieve metadata for the song
// const songMetadata = metadataMap.get(songWithoutExtension);

// 	if (!songMetadata) {
// 		console.error(`Metadata not found for song: ${song}`);
// 		return;
// 	}
// 	switch (action) {
// 		case "add":
// 			// Add to download list
// 			const trackElement = createTrackElement(
// 				songMetadata.name,
// 				songMetadata.artist,
// 				songMetadata.cover_url
// 			);
// 			document
// 				.getElementById("progress-window")
// 				.appendChild(trackElement);
// 			break;
// 		case "remove":
// 			// Remove from download list
// 			// Implement removal logic here
// 			break;
// 		case "clear":
// 			// Clear download list
// 			// Implement clear logic here
// 			break;
// 		}
// }

// Update file list whenever changes are detected (using wrappers to pass the action)
// window.electron.onFileListInitial((files) => updateProgressListUI(files, "initial"));
window.electron.onFileAdded((file) => updateProgressListUI(file, "add"));
window.electron.onFileRemoved((file) => updateProgressListUI(file, "remove"));

let downloadedFiles = new Set(); // Global set to track files

// Update the download file list UI
function updateProgressListUI(data, action) {
	if (downloadType === "download") {
		if (action === "initial") {
			for (let song of data.songs) {
				trackElement = createTrackUIElement(
					song.name,
					song.artist,
					song.cover_url,
					false,
					song.song_id
				);
				document
					.getElementById("progress-window")
					.appendChild(trackElement);
			}
		} else if (action === "add") {
			// Here, data is a string like "song arist - song name.mp3"
			if (!downloadedFiles.has(data)) {
				downloadedFiles.add(data);
				const songMetadata = metadataMap.get(
					stripFileExtension(data),
					{}
				);
				if (songMetadata) {
					trackElement = createTrackUIElement(
						songMetadata.name,
						songMetadata.artist,
						songMetadata.cover_url
					);
					document
						.getElementById("progress-window")
						.appendChild(trackElement);
				} else {
					console.log("Metadata not found for:", data);
				}
			}
		} else if (action === "remove") {
			// data is a single file string
			downloadedFiles.delete(data);
		}
	} else if ((downloadType = "sync")) {
		// Do something
	}

	// Update the UI with the current number of downloaded files
	document.getElementById("number-downloaded").textContent =
		downloadedFiles.size;
}

function createTrackUIElement(trackName, artistName, imageUrl) {
	let trackDiv = document.createElement("div");
	trackDiv.classList.add("progress-track", "progress-item");

	// if (downloaded) {
	// 	trackDiv.classList.add("progress-downloaded");
	// } else trackDiv.classList.add("progress-downloading");

	let img = document.createElement("img");
	if (imageUrl) {
		img.src = imageUrl;
	} else {
		img.src = "static/resources/default-cover.png"; // Fallback image if imageUrl is undefined
	}
	img.alt = `${trackName} cover`;
	img.width = 50;

	let textDiv = document.createElement("div");

	let trackP = document.createElement("p");
	trackP.classList.add("track-name");
	trackP.textContent = trackName;

	let artistP = document.createElement("p");
	artistP.classList.add("track-artist");
	artistP.textContent = artistName;

	textDiv.appendChild(trackP);
	textDiv.appendChild(artistP);

	trackDiv.appendChild(img);
	trackDiv.appendChild(textDiv);

	return trackDiv;
}

document
	.getElementById("cancel-button")
	.addEventListener("click", canceldownload);

async function canceldownload() {
	const response = await fetch("/stop-download", {
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

	document.getElementById("go-button").style.display = "flex";
	document.getElementById("cancel-button").style.display = "none";
	document.getElementById("progress").style.display = "none";
	window.electron.stopWatching();
}

document.getElementById("spotify-login").addEventListener("click", () => {
	window.location.href = "/login"; // Replace with the desired URL
});

// Function to get query parameters from URL
function getQueryParam(param) {
	const urlParams = new URLSearchParams(window.location.search);
	return urlParams.get(param);
}

// Get user_name and access_token from the URL
window.onload = function () {
	const userName = getQueryParam("user_name"); // Default to 'Guest' if no user_name
	const accessToken = getQueryParam("access_token");

	if (userName) {
		// Use these variables as needed in JavaScript
		document.getElementById("spotify-username").textContent = `${userName}`;
	}

	if (accessToken) {
		// Do something with the access token if needed (e.g., store it or send it in API requests)
		console.log("Access Token:", accessToken);
	}
};
