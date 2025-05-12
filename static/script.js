document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const historyLog = document.getElementById('history-log');
    const connectionStatus = document.getElementById('connection-status');
    const debugMenu = document.getElementById('debug-menu');
    const objectivesList = document.getElementById('objectives-list');

    // Global turn counter for unique IDs
    let turnIdCounter = 0;

    // Generate a random session ID
    const sessionId = Math.random().toString(36).substring(2, 15);

    // WebSocket connection
    let socket;
    let isConnected = false;
    let currentTurnElement = null;
    let currentNarrationElement = null;
    let currentChoicesElement = null;
    let isGameFinished = false;
    const PLACEHOLDER_IMG_SRC = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

    // Typing effect settings
    const TYPING_DELAY_MS = 20; // milliseconds between characters
    let activeTypingAbortController = null; // To cancel ongoing typing if needed

    // State for coordinating narration and choices display
    let turnNarrationStatus = {}; // E.g., { 0: "typing" | "complete" }
    let pendingChoices = {};    // E.g., { 0: [...] }

    // Debug menu state
    let isDebugMenuVisible = false;

    // Toggle debug menu with F8
    document.addEventListener('keydown', (e) => {
        if (e.key === 'F8') {
            isDebugMenuVisible = !isDebugMenuVisible;
            debugMenu.style.display = isDebugMenuVisible ? 'block' : 'none';
        }
    });

    // Update objectives list
    function updateObjectivesList(objectives) {
        objectivesList.innerHTML = '';
        objectives.forEach(obj => {
            const li = document.createElement('li');
            li.textContent = obj.objective;
            li.className = obj.finished ? 'completed' : 'pending';
            objectivesList.appendChild(li);
        });
    }

    // Helper: format markdown bold and insert double line breaks after sentences
    function formatNarration(text) {
        return text
            .replace(/\*\*(.+?)\*\*/g, '<span class="md-bold">$1</span>')
            .replace(/\*(.+?)\*/g, '<span class="md-bold">$1</span>')
            .replace(/([.!?])\s*/g, '$1<br><br>');
    }

    // Cursor management functions
    function ensureCursor(narrationElement) {
        if (!narrationElement) return;
        let cursor = narrationElement.querySelector('.cursor');
        if (!cursor) {
            cursor = document.createElement('span');
            cursor.className = 'cursor';
            narrationElement.appendChild(cursor);
        }
        // Ensure it's the last child
        if (narrationElement.lastChild !== cursor) {
            narrationElement.appendChild(cursor);
        }
    }

    function removeCursor(narrationElement) {
        if (!narrationElement) return;
        const cursor = narrationElement.querySelector('.cursor');
        if (cursor) {
            cursor.remove();
        }
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
            turnIdCounter = 0;
            console.log("[WebSocket Open] Initial turnIdCounter set to 0.");
            isGameFinished = false;
            historyLog.innerHTML = '';
            turnNarrationStatus = {}; // Reset on new connection
            pendingChoices = {};    // Reset on new connection
            createNewTurnElement(turnIdCounter);
        };
        socket.onclose = () => {
            isConnected = false;
            connectionStatus.textContent = 'Disconnected. Attempting to reconnect...';
            connectionStatus.style.color = '#ff4d4d';
            console.log("[WebSocket Close] History log will NOT be cleared automatically by onclose. Reconnect will clear.");
            if (activeTypingAbortController) activeTypingAbortController.abort(); // Cancel typing on disconnect
            setTimeout(connectWebSocket, 3000);
        };
        socket.onerror = (error) => {
            console.error('[WebSocket Error]:', error); 
            connectionStatus.textContent = 'Error connecting';
            connectionStatus.style.color = '#ff4d4d';
            if (activeTypingAbortController) activeTypingAbortController.abort(); // Cancel typing on error
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
                console.log("[handleServerMessage] Received 'text' type (potentially legacy):", data.content);
                break;
            case 'narration_block':
                handleNarrationBlockMessage(data);
                break;
            case 'image':
                handleImageMessage(data);
                break;
            case 'choices':
                handleChoicesMessage(data.content, data.turn_id);
                break;
            case 'objectives':
                updateObjectivesList(data.content);
                break;
            case 'error':
                handleErrorMessage(data);
                break;
            case 'game_end':
                handleGameEndMessage(data);
                break;
            default:
                console.warn('[WebSocket Warning] Unknown message type:', data.type);
        }
    }

    // Creates a new container for a turn in the history log
    function createNewTurnElement(turnIdForElement) {
        if (isGameFinished && turnIdForElement > 0) {
            console.log(`[createNewTurnElement] Game has finished. Not creating new turn element for ID: ${turnIdForElement}.`);
            return null;
        }
        
        console.log(`[createNewTurnElement] Creating turn element for ID: ${turnIdForElement}`);
        const turnElement = document.createElement('div');
        turnElement.className = 'turn-container';
        turnElement.dataset.turnId = turnIdForElement;

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
        imageElement.onerror = () => console.warn(`Placeholder image failed to load for turn ${turnIdForElement}?`);

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
        turnElement.appendChild(imageContainer);
        turnElement.appendChild(contentContainer);

        // Add to log and reset state
        historyLog.appendChild(turnElement);
        scrollToBottom();
        return turnElement;
    }

    // Modified function to simulate typing for narration blocks
    async function handleNarrationBlockMessage(data) {
        console.log(`[handleNarrationBlockMessage] Received narration for turn_id: ${data.turn_id}`);
        
        // Cancel any previous typing animation for this turn or globally
        if (activeTypingAbortController) {
            activeTypingAbortController.abort();
            console.log("[handleNarrationBlockMessage] Aborted previous typing animation.");
        }
        activeTypingAbortController = new AbortController();
        const signal = activeTypingAbortController.signal;

        let targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${data.turn_id}"]`);
        if (!targetTurnElement && data.turn_id === 0) {
            console.log("[handleNarrationBlockMessage] Turn 0 element not found, creating it.");
            targetTurnElement = createNewTurnElement(0);
        }
        if (!targetTurnElement) {
            console.error(`[handleNarrationBlockMessage] Could not find or create turn container for turn_id: ${data.turn_id}.`);
            return;
        }

        const narrationElement = targetTurnElement.querySelector('.turn-narration');
        if (narrationElement) {
            narrationElement.innerHTML = ''; // Clear previous content before typing
            let currentTypedText = '';

            try {
                for (let i = 0; i < data.content.length; i++) {
                    if (signal.aborted) {
                        console.log("[handleNarrationBlockMessage] Typing aborted for turn_id:", data.turn_id);
                        narrationElement.innerHTML = formatNarration(data.content); // Show full text if aborted
                        removeCursor(narrationElement);
                        turnNarrationStatus[data.turn_id] = "complete"; // Mark as complete even if aborted
                        console.log("[Narration] Typing aborted for turn:", data.turn_id);
                        // Check if choices were pending for this turn
                        if (pendingChoices[data.turn_id]) {
                            renderChoices(data.turn_id, pendingChoices[data.turn_id]);
                            delete pendingChoices[data.turn_id];
                        }
                        return;
                    }
                    currentTypedText += data.content[i];
                    narrationElement.innerHTML = formatNarration(currentTypedText); // Apply formatting as we type
                    ensureCursor(narrationElement);
                    scrollToBottom();
                    await new Promise(resolve => setTimeout(resolve, TYPING_DELAY_MS));
                }
                removeCursor(narrationElement); // Remove cursor when typing is complete
                narrationElement.innerHTML = formatNarration(data.content); // Ensure final full text with proper formatting
                turnNarrationStatus[data.turn_id] = "complete";
                console.log("[Narration] Typing complete for turn:", data.turn_id);

                // Narration finished, check for and render pending choices
                if (pendingChoices[data.turn_id]) {
                    console.log("[Narration] Pending choices found for turn:", data.turn_id, "Rendering now.");
                    renderChoices(data.turn_id, pendingChoices[data.turn_id]);
                    delete pendingChoices[data.turn_id];
                }

            } catch (error) {
                if (error.name === 'AbortError') {
                    console.log("[handleNarrationBlockMessage] Typing explicitly aborted (already handled) for turn_id:", data.turn_id);
                } else {
                    console.error("[handleNarrationBlockMessage] Error during typing simulation:", error);
                    narrationElement.innerHTML = formatNarration(data.content); // Fallback to full text on error
                }
                removeCursor(narrationElement);
                turnNarrationStatus[data.turn_id] = "complete";
                if (pendingChoices[data.turn_id]) { // Also check on general error
                    renderChoices(data.turn_id, pendingChoices[data.turn_id]);
                    delete pendingChoices[data.turn_id];
                }
            }

        } else {
            console.error(`[handleNarrationBlockMessage] Narration element not found in turn_id: ${data.turn_id}`);
        }
    }

    // Handle image messages
    function handleImageMessage(data) {
        console.log(`[handleImageMessage] Received image URL for turn_id: ${data.turn_id}`);
        let targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${data.turn_id}"]`);
        if (!targetTurnElement && data.turn_id === 0) {
            console.log("[handleImageMessage] Turn 0 element not found, creating it for image.");
            targetTurnElement = createNewTurnElement(0);
        }
        if (!targetTurnElement) {
            console.error(`[handleImageMessage] Could not find or create turn container for turn_id: ${data.turn_id}`);
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
    function handleChoicesMessage(choices, originating_turn_id) {
        console.log(`[handleChoicesMessage] Received choices for turn_id (originating): ${originating_turn_id}`);
        pendingChoices[originating_turn_id] = choices;
        
        // If narration for this turn is already complete, render choices now.
        if (turnNarrationStatus[originating_turn_id] === "complete") {
            console.log("[Choices] Narration already complete for turn:", originating_turn_id, "Rendering choices.");
            renderChoices(originating_turn_id, choices);
            delete pendingChoices[originating_turn_id]; // Clear after rendering
        } else {
            console.log("[Choices] Narration not yet complete for turn:", originating_turn_id, "Choices are pending.");
        }
    }

    // New function to actually render the choices to the DOM.
    function renderChoices(turn_id, choices_data) {
        console.log(`[renderChoices] Rendering choices for turn_id: ${turn_id}`);
        const targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${turn_id}"]`);
        if (!targetTurnElement) {
            console.error(`[renderChoices] Could not find turn container for turn_id: ${turn_id}`);
            return;
        }
        const choicesElement = targetTurnElement.querySelector('.turn-choices');
        if (!choicesElement) {
            console.error(`[renderChoices] Choices element not found in turn_id: ${turn_id}`);
            return;
        }

        // Abort any active typing for this turn before showing choices (safety, should be done by narration complete)
        if (activeTypingAbortController && turnNarrationStatus[turn_id] === "typing") {
             console.warn("[renderChoices] Aborting typing for turn", turn_id, "as choices are being rendered.");
             activeTypingAbortController.abort(); 
        }
        removeCursor(targetTurnElement.querySelector('.turn-narration')); // Ensure cursor is gone

        choicesElement.innerHTML = ''; 
        choices_data.forEach(choice => {
            const button = document.createElement('button');
            button.className = 'choice-button';
            button.textContent = choice;
            const turnIdForNewDataRequest = turn_id + 1; 
            button.addEventListener('click', () => {
                if (isConnected && !isGameFinished) {
                    button.classList.add('selected');
                    const buttonsInTurn = targetTurnElement.querySelectorAll('.choice-button');
                    buttonsInTurn.forEach(btn => btn.disabled = true);
                    const messageToSend = { choice: choice, turn_id: turnIdForNewDataRequest }; 
                    console.log(`[Choice Click] Preparing to send:`, JSON.stringify(messageToSend)); 
                    socket.send(JSON.stringify(messageToSend));
                    turnIdCounter = turnIdForNewDataRequest;
                    createNewTurnElement(turnIdCounter); 
                }
            });
            choicesElement.appendChild(button);
        });
        scrollToBottom();
    }

    // Handle error messages
    function handleErrorMessage(data) {
        const errorMsg = data.content;
        const turnId = data.turn_id;
        console.error(`[Server Error] Turn ID: ${turnId}, Message: ${errorMsg}`);

        // Special handling for image generation errors
        const isImageError = errorMsg.startsWith("Error generating image:") || errorMsg.startsWith("Image generation failed");
        let targetTurnElement = historyLog.querySelector(`.turn-container[data-turn-id="${turnId}"]`);

        // Attempt to create turn element if it doesn't exist, especially for turn 0 errors on init.
        if (!targetTurnElement && turnId === 0) {
            console.log("[handleErrorMessage] Turn 0 element not found, creating it for error message.");
            targetTurnElement = createNewTurnElement(0);
        }

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
            console.error(`[handleErrorMessage] Could not find or create target turn element for turn_id: ${turnId} to display error.`);
        }
        scrollToBottom(); // Scroll regardless of error type
    }

    // Handle game end messages
    function handleGameEndMessage(data) {
        console.log("[handleGameEndMessage] Received game_end message:", data.message);
        isGameFinished = true;

        // Determine the ID of the last turn that received choices or narration.
        // This might be (turnIdCounter - 1) if createNewTurnElement was called for the *next* turn after last choice.
        // Or it could be just turnIdCounter if no new element was pre-created.
        // Let's find the actual last turn container in the log.
        const allTurnContainers = historyLog.querySelectorAll('.turn-container');
        let lastTurnElementWithMessage = null;
        if (allTurnContainers.length > 0) {
            lastTurnElementWithMessage = allTurnContainers[allTurnContainers.length - 1];
        }

        if (lastTurnElementWithMessage) {
            let choicesContainer = lastTurnElementWithMessage.querySelector('.turn-choices');
            if (choicesContainer) {
                choicesContainer.innerHTML = ''; 
                const endMessageElement = document.createElement('div');
                endMessageElement.className = 'game-end-message';
                endMessageElement.textContent = "THE END!";
                choicesContainer.appendChild(endMessageElement);
            } else {
                 // If no choices container, append to the turn element itself or create one.
                const endMessageElement = document.createElement('div');
                endMessageElement.className = 'game-end-message';
                endMessageElement.textContent = "THE END!";
                lastTurnElementWithMessage.querySelector('.turn-content').appendChild(endMessageElement);
            }
        } else {
            const endMessageElement = document.createElement('div');
            endMessageElement.className = 'game-end-message';
            endMessageElement.textContent = "THE END!";
            historyLog.appendChild(endMessageElement); 
            console.warn("[handleGameEndMessage] Could not find any turn container. Appended THE END to history log directly.");
        }
        scrollToBottom();
    }

    // Utility to scroll history log to bottom
    function scrollToBottom() {
        // Scroll horizontally to the end to show the latest turn
        historyLog.scrollLeft = historyLog.scrollWidth;
    }

    // Initialize WebSocket connection
    connectWebSocket();
}); 