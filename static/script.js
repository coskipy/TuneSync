const statusDiv = document.getElementById("status");
let metadataMap = null;
let downloadType = "download";
let OAuthToken = null;
let songsExpected = 0;

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
			songsExpected = metadataResult.songs.length;
			setSongsExpectedText();
		} else {
			statusDiv.textContent = `Error: ${metadataResult.message}`;
			statusDiv.style.color = "red";
		}

		document.getElementById("progress-title").textContent =
			"Downloading...";
		statusDiv.textContent = "Downloading...";

		document.getElementById("loading-bar-progress").style.display = "flex";

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
			console.log(message);
			statusDiv.textContent = `Error: See console`;
			statusDiv.style.color = "red";
		}

		document.getElementById("progress-title").textContent =
			"Download complete!";

		document.getElementById("loading-bar-container").style.display = "none";

		document.getElementById("go-button").style.display = "flex";
		document.getElementById("cancel-button").style.display = "none";

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
	// console.log(result.message);
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

function setSongsExpectedText() {
	document.getElementById("number-expected").textContent = songsExpected;
}

function stripFileExtension(filename) {
	// Only remove extension if the period is at the end of the string
	return filename.replace(/(?<=^.*)(?=\.[^/.]+$)/, "");
}

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

	if (downloadedFiles.size === songsExpected) {
		document.getElementById("progress-title").textContent =
			"Finishing touches...";
		document.getElementById("loading-bar-container").style.display = "none";
	}

	// Hide the infinite loading bar
	document.getElementById("loading-bar-infinite").style.display = "none";

	// Update the progress bar
	const progress = (downloadedFiles.size / songsExpected) * 100;

	document.getElementById(
		"loading-bar-progress"
	).style.width = `${progress}%`;
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
};

document
	.getElementById("select-playlist")
	.addEventListener("click", async () => {
		const playlist_selection_window = document.getElementById(
			"playlist-selection-window"
		);

		if (playlist_selection_window.style.display === "none") {
			playlist_selection_window.style.display = "flex";
		} else {
			playlist_selection_window.style.display = "none";
		}

		try {
			// Fetch the playlists from the server
			const response = await fetch("/get-user-playlists", {
				method: "GET",
				headers: { "Content-Type": "application/json" },
			});

			// Check if the response is successful
			if (!response.ok) {
				throw new Error("Failed to fetch playlists", response.error);
			}

			// Parse the JSON response
			const user_playlists = await response.json();

			console.log(user_playlists);
			for (let playlist of user_playlists) {
				if (playlist.images == null) {
					coverUrl = "static/resources/default-playlist-cover.png";
				} else coverUrl = playlist.images[0].url;

				playlistElement = createPlaylistUIElement(
					playlist.name,
					playlist.owner.display_name,
					coverUrl,
					playlist.external_urls.spotify,
					playlist.tracks.total
				);

				document
					.getElementById("playlist-selection-window")
					.appendChild(playlistElement);
			}
		} catch (error) {
			console.error("Error fetching playlists:", error);
		}
	});

function createPlaylistUIElement(
	playlistName,
	creatorName,
	imageUrl,
	playlistUrl,
	totalTracks
) {
	let playlistDiv = document.createElement("div");
	playlistDiv.classList.add("playlist-item");

	let img = document.createElement("img");
	if (imageUrl) {
		img.src = imageUrl;
	} else {
		img.src = "static/resources/default-cover.png"; // Fallback image if imageUrl is undefined
	}
	img.alt = `${playlistName} cover`;

	let img_container = document.createElement("div");
	img_container.classList.add("playlist-img-container");
	img_container.appendChild(img);

	let textDiv = document.createElement("div");
	textDiv.classList.add("playlist-text");

	let playlistP = document.createElement("p");
	playlistP.classList.add("playlist-name");
	playlistP.textContent = playlistName;

	let artistP = document.createElement("p");
	artistP.classList.add("playlist-creator");
	artistP.textContent = creatorName + " • " + totalTracks + " songs";

	let urlP = document.createElement("p");
	urlP.textContent = playlistUrl;
	urlP.classList.add("playlist-url");
	urlP.style.display = "none";

	textDiv.appendChild(playlistP);
	textDiv.appendChild(artistP);

	playlistDiv.appendChild(img_container);
	playlistDiv.appendChild(textDiv);
	playlistDiv.appendChild(urlP);

	return playlistDiv;
}

document
	.getElementById("playlist-selection-window")
	.addEventListener("click", (e) => {
		removePlaylistSelection();
		// Check if the clicked element has the 'playlist-item' class
		const item = e.target.closest(".playlist-item");
		if (!item) return; // If not clicking on a playlist-item, ignore

		// Toggle selection class
		item.classList.toggle("playlist-item-selected");

		url = item.querySelector(".playlist-url").textContent;
		document.getElementById("url").value = url;
	});

function removePlaylistSelection() {
	document
		.querySelectorAll(".playlist-item-selected")
		.forEach((item) => item.classList.remove("playlist-item-selected"));
}
