document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const historyLog = document.getElementById('history-log');
    const connectionStatus = document.getElementById('connection-status');

    // Global turn counter for unique IDs
    let turnIdCounter = 0;

    // Generate a random session ID
    const sessionId = Math.random().toString(36).substring(2, 15);

    // WebSocket connection
    let socket;
    let isConnected = false;
    let currentTurnElement = null; // Reference to the current turn's container div
    let currentNarrationElement = null; // Reference to the current turn's narration div
    let currentChoicesElement = null; // Reference to the current turn's choices div
    let cursorElement = null;

    // State for parsing narration stream character-by-character
    let fullResponseText = ''; // Accumulates the raw JSON stream for optional final sync
    let isParsingNarration = false;
    let stringBuffer = '';
    let escapeNextChar = false;
    const NARRATION_START_PATTERN = '"narration": "';
    const PLACEHOLDER_IMG_SRC = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
    let narrationBuffer = ''; // buffer for dynamic formatting

    // Helper: format markdown bold and insert double line breaks after sentences
    function formatNarration(text) {
        return text
            .replace(/\*\*(.+?)\*\*/g, '<span class="md-bold">$1</span>')
            .replace(/\*(.+?)\*/g, '<span class="md-bold">$1</span>')
            .replace(/([.!?])\s*/g, '$1<br><br>');
    }

    // Connect to WebSocket
    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${sessionId}`;

        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            isConnected = true;
            connectionStatus.textContent = 'Connected';
            connectionStatus.style.color = '#4dff4d';
            turnIdCounter = 0; // Reset turn ID counter for the new session/connection
            console.log("[WebSocket Open] Reset turnIdCounter to 0.");
            createNewTurnElement(); // Create the first turn element on connect
        };

        socket.onclose = () => {
            isConnected = false;
            connectionStatus.textContent = 'Disconnected. Reconnecting...';
            connectionStatus.style.color = '#ff4d4d';
            // Clear the history log on disconnect before reconnecting
            console.log("[WebSocket Close] Clearing history log.");
            historyLog.innerHTML = '';
            setTimeout(connectWebSocket, 3000);
        };

        socket.onerror = (error) => {
            console.error('[WebSocket Error]:', error); 
            connectionStatus.textContent = 'Error connecting';
            connectionStatus.style.color = '#ff4d4d';
        };

        socket.onmessage = (event) => {
            try {
                 const data = JSON.parse(event.data);
                 handleServerMessage(data);
            } catch (e) {
                console.error("[WebSocket Error] Failed to parse message JSON:", e, "Raw data:", event.data);
            }
        };
    }

    // Main message handler
    function handleServerMessage(data) {
         switch (data.type) {
            case 'text':
                handleTextMessage(data.content);
                break;
            case 'image':
                handleImageMessage(data);
                break;
            case 'choices':
                handleChoicesMessage(data.content);
                break;
            case 'error':
                handleErrorMessage(data);
                break;
            default:
                console.warn('[WebSocket Warning] Unknown message type:', data.type);
        }
    }

    // Creates a new container for a turn in the history log
    function createNewTurnElement() {
        const turnId = turnIdCounter++; // Assign and increment
        currentTurnElement = document.createElement('div');
        currentTurnElement.className = 'turn-container';
        currentTurnElement.dataset.turnId = turnId; // Store turn ID on the element
        console.log(`[createNewTurnElement] Creating turn element with ID: ${turnId}`);

        // Image container
        const imageContainer = document.createElement('div');
        imageContainer.className = 'turn-image-container';
        const imageElement = document.createElement('img');
        imageElement.className = 'turn-image';
        // Set placeholder image
        imageElement.src = PLACEHOLDER_IMG_SRC;
        imageElement.alt = 'Scene Image';
        imageContainer.appendChild(imageElement);
        // Add pixel-art loader overlay on top of image until real load
        const loaderElement = document.createElement('div');
        loaderElement.className = 'pixel-loader';
        const loaderPixel = document.createElement('div');
        loaderElement.appendChild(loaderPixel);
        imageContainer.appendChild(loaderElement);
        console.log("[createNewTurnElement] Added image placeholder and loader.");
        // Add basic error handler for placeholder (just in case)
        imageElement.onerror = () => console.warn(`Placeholder image failed to load for turn ${turnId}?`);

        // Content container (narration + choices)
        const contentContainer = document.createElement('div');
        contentContainer.className = 'turn-content';
        
        currentNarrationElement = document.createElement('div');
        currentNarrationElement.className = 'turn-narration';
        
        currentChoicesElement = document.createElement('div');
        currentChoicesElement.className = 'turn-choices';

        contentContainer.appendChild(currentNarrationElement);
        contentContainer.appendChild(currentChoicesElement);

        // Assemble turn container
        currentTurnElement.appendChild(imageContainer);
        currentTurnElement.appendChild(contentContainer);

        // Add to log and reset state
        historyLog.appendChild(currentTurnElement);
        resetNarrationParsingState(); 
        scrollToBottom();
    }

    // Reset only the parsing state for a new message stream
    function resetNarrationParsingState() {
        fullResponseText = '';
        isParsingNarration = false;
        stringBuffer = '';
        escapeNextChar = false;
        cursorElement = null; // Reset cursor reference
        narrationBuffer = ''; // Reset narration buffer
    }

    // Handle text messages (tokens) - Character-by-character parser
    function handleTextMessage(token) {
        if (!token || !currentTurnElement) return;
        fullResponseText += token;

        for (let i = 0; i < token.length; i++) {
            const char = token[i];

            if (isParsingNarration) {
                if (escapeNextChar) {
                    appendNarrationCharToUI(char);
                    escapeNextChar = false;
                } else if (char === '\\') {
                    escapeNextChar = true;
                } else if (char === '"') {
                    isParsingNarration = false;
                } else {
                    appendNarrationCharToUI(char);
                }
            } else {
                stringBuffer += char;
                if (stringBuffer.length > NARRATION_START_PATTERN.length) {
                    stringBuffer = stringBuffer.substring(stringBuffer.length - NARRATION_START_PATTERN.length);
                }
                
                if (stringBuffer === NARRATION_START_PATTERN) {
                    isParsingNarration = true;
                    escapeNextChar = false;
                    stringBuffer = '';
                }
            }
        }
    }

    // Appends a single character to the current turn's narration UI element
    function appendNarrationCharToUI(char) {
        if (!char || !currentNarrationElement) return;
        // Append char to buffer and render with formatting
        narrationBuffer += char;
        currentNarrationElement.innerHTML = formatNarration(narrationBuffer);
        ensureCursor(); // Ensure cursor at end
        scrollToBottom(); // Scroll as formatted text updates
    }

    // Ensures the cursor element exists and is appended to the current narration element
    function ensureCursor() {
         if (!cursorElement) {
            cursorElement = document.createElement('span');
            cursorElement.className = 'cursor';
        }
        // Append cursor if it's not already in the current narration element or not the last child
        if (currentNarrationElement && (!currentNarrationElement.contains(cursorElement) || currentNarrationElement.lastChild !== cursorElement)) {
             currentNarrationElement.appendChild(cursorElement);
        }
    }

    // Handle image messages
    function handleImageMessage(data) { // data = { content: imageUrl, turn_id: id }
        console.log(`[handleImageMessage] Received image URL for turn_id: ${data.turn_id}`);
        const targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${data.turn_id}"]`);
        if (!targetTurnElement) {
            console.error(`[handleImageMessage] Could not find turn container for turn_id: ${data.turn_id}`);
            return;
        }

        const imageContainer = targetTurnElement.querySelector('.turn-image-container');
        const imageElement = imageContainer?.querySelector('.turn-image');
        const loaderElement = imageContainer?.querySelector('.pixel-loader');

        if (imageElement && imageContainer) {
            const loaderToRemove = loaderElement; // Capture specific loader

            // Remove loader when real image loads or fails
            imageElement.onload = () => { 
                console.log(`[handleImageMessage] Real image loaded for turn_id: ${data.turn_id}.`);
                if (loaderToRemove && loaderToRemove.parentNode) { 
                    loaderToRemove.remove(); 
                }
            };
            imageElement.onerror = () => { 
                console.error(`[handleImageMessage] Real image failed to load for turn_id: ${data.turn_id}.`);
                if (loaderToRemove && loaderToRemove.parentNode) { 
                    loaderToRemove.remove(); 
                }
            };
            // Assign actual scene image URL
            imageElement.src = `data:image/png;base64,${data.content}`; // Use data.content which is the URL
            console.log(`[handleImageMessage] Set image src for turn_id: ${data.turn_id}.`);
        } else {
            console.error(`[handleImageMessage] Error: imageElement or imageContainer not found for turn_id: ${data.turn_id}.`);
        }
    }

    // Handle choices messages - signaling end of turn
    function handleChoicesMessage(choices) {
        if (!currentTurnElement || !currentChoicesElement) return;
        
        isParsingNarration = false; // Ensure parsing stops

        // Remove cursor
        if (cursorElement && currentNarrationElement && currentNarrationElement.contains(cursorElement)) {
            currentNarrationElement.removeChild(cursorElement);
            cursorElement = null;
        }

        // Final Sync (Optional, but good practice)
        try {
            const finalJson = JSON.parse(fullResponseText);
            if (finalJson.narration && currentNarrationElement) {
                console.warn("[Choices] Final Narration Sync needed & applied.");
                currentNarrationElement.innerHTML = formatNarration(finalJson.narration);
            }
        } catch (e) {
            // Ignore final sync errors
        }

        // Display choices in the current turn's choice container
        currentChoicesElement.innerHTML = ''; // Clear just in case
        const turnElementForButtons = currentTurnElement; // Capture ref for closure

        choices.forEach(choice => {
            const button = document.createElement('button');
            button.className = 'choice-button';
            button.textContent = choice;

            // Get the ID of the *next* turn element that will be created after click
            const nextTurnId = turnIdCounter; 

            button.addEventListener('click', () => {
                if (isConnected) {
                    // Highlight the clicked button
                    button.classList.add('selected');
                    // Disable buttons in the specific turn container where the click happened
                    const buttonsInTurn = turnElementForButtons.querySelectorAll('.choice-button');
                    buttonsInTurn.forEach(btn => btn.disabled = true);
                    
                    // Log the exact object being sent
                    const messageToSend = { choice: choice, turn_id: nextTurnId };
                    console.log(`[Choice Click] Preparing to send:`, JSON.stringify(messageToSend)); 

                    // Send choice to server, including the ID for the *next* turn
                    console.log(`[Choice Click] Sending choice for next turn_id: ${nextTurnId}`);
                    socket.send(JSON.stringify(messageToSend));
                    
                    // Create structure for the *next* turn
                    createNewTurnElement();
                }
            });
            currentChoicesElement.appendChild(button);
        });
        scrollToBottom();
    }

    // Handle error messages
    function handleErrorMessage(data) { // data = { content: errorMsg, turn_id: id }
        const errorMsg = data.content;
        const turnId = data.turn_id;
        console.error(`[Server Error] Turn ID: ${turnId}, Message: ${errorMsg}`);

        // Special handling for image generation errors
        const isImageError = errorMsg.startsWith("Error generating image:") || errorMsg.startsWith("Image generation failed");
        const targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${turnId}"]`);

        if (isImageError && targetTurnElement) {
            const imageContainer = targetTurnElement.querySelector('.turn-image-container');
            const loaderElement = imageContainer?.querySelector('.pixel-loader');

            // Remove the loader if it exists
            if (loaderElement) {
                loaderElement.remove();
            }
            // Keep placeholder image, add icon + tooltip
            if (imageContainer && !imageContainer.querySelector('.image-error-icon')) { // Prevent adding multiple icons
                const icon = document.createElement('span');
                icon.className = 'image-error-icon';
                icon.textContent = '🐞'; 

                const tooltip = document.createElement('div');
                tooltip.className = 'image-error-tooltip';
                tooltip.textContent = errorMsg; // Show the actual error
                tooltip.style.display = 'none'; // Initially hidden

                icon.addEventListener('mouseover', () => { tooltip.style.display = 'block'; });
                icon.addEventListener('mouseout', () => { tooltip.style.display = 'none'; });

                imageContainer.appendChild(icon);
                imageContainer.appendChild(tooltip);
            }
        }
        // Existing handling for other/general errors (prepend to narration)
        else if (targetTurnElement) { // Add general errors to narration of the correct turn
            const narrationElement = targetTurnElement.querySelector('.turn-narration');
            if (narrationElement) {
                const errorElement = document.createElement('div');
                errorElement.className = 'error-message';
                errorElement.style.color = 'var(--accent-color)'; // Make it stand out
                errorElement.textContent = `Error: ${errorMsg}`;
                narrationElement.prepend(errorElement);
            } else {
                console.error(`[handleErrorMessage] Could not find narration element for turn_id: ${turnId} to display general error.`);
            }
        } else {
            console.error(`[handleErrorMessage] Could not find target turn element for turn_id: ${turnId} to display error.`);
        }
        scrollToBottom(); // Scroll regardless of error type
    }

    // Utility to scroll history log to bottom
    function scrollToBottom() {
        // Scroll horizontally to the end to show the latest turn
        historyLog.scrollLeft = historyLog.scrollWidth;
    }

    // Initialize WebSocket connection
    connectWebSocket();
}); 