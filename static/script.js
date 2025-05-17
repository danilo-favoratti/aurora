document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const historyLog = document.getElementById('history-log');
    const connectionStatus = document.getElementById('connection-status');
    const objectivesList = document.getElementById('objectives-list');
    const savePdfButton = document.getElementById('save-pdf-button');

    // New Top Menu elements
    const topMenuContainer = document.getElementById('top-menu-container');
    const topMenuToggleButton = document.getElementById('top-menu-toggle-button');

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

    // Check initial screen width to set menu state - REMOVED as menu now starts closed by default
    /*
    if (topMenuContainer && window.innerWidth < 768) {
        topMenuContainer.classList.remove('open');
    }
    */

    // Toggle new top menu
    if (topMenuToggleButton && topMenuContainer) {
        topMenuToggleButton.addEventListener('click', () => {
            topMenuContainer.classList.toggle('open');
            // Adjust main layout margin if menu is permanently reserving space when open
            // For now, CSS handles visibility and main layout has a fixed margin-top
        });
    }

    // Update objectives list
    function updateObjectivesList(objectives) {
        objectivesList.innerHTML = '';
        if (!objectives || objectives.length === 0) {
            const li = document.createElement('li');
            if (turnIdCounter === 0) {
                li.textContent = "Please select a theme to begin your journey.";
            } else {
                li.textContent = "No active objectives.";
            }
            objectivesList.appendChild(li);
        } else {
            objectives.forEach(obj => {
                const li = document.createElement('li');
                li.className = obj.finished ? 'completed' : 'pending';

                const textContainer = document.createElement('span');
                textContainer.className = 'objective-text-container';

                let mainObjectiveText = obj.objective;
                textContainer.appendChild(document.createTextNode(mainObjectiveText));
                
                li.appendChild(textContainer);
                objectivesList.appendChild(li);
            });
        }

        // Blink menu button if objectives updated and menu is closed
        if (topMenuToggleButton && topMenuContainer && !topMenuContainer.classList.contains('open')) {
            topMenuToggleButton.classList.add('blink-attention');
            setTimeout(() => {
                topMenuToggleButton.classList.remove('blink-attention');
            }, 600); // Duration should match CSS animation (e.g., 0.6s)
        }
    }

    // Helper: format markdown bold and insert double line breaks after sentences
    function formatNarration(text) {
        return text
            .replace(/\*\*(.+?)\*\*/g, '<span class="md-bold">$1</span>')
            .replace(/\*(.+?)\*/g, '<span class="md-bold">$1</span>')
            .replace(/([.!?])\s*/g, '$1<br><br>')
            .replace(/(:)\s*/g, '$1<br><br>')
            .replace(/(;)\s*/g, '$1<br><br>')
            .replace(/(\.\.\.)\s*/g, '$1<br><br>');
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
            if (objectivesList) objectivesList.innerHTML = '';
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

    // PDF Generation Function
    async function generatePdf() {
        if (isGameFinished || turnIdCounter > 0) { 
            console.log("[PDF] Starting PDF generation...");
            connectionStatus.textContent = "Generating PDF...";

            const { jsPDF } = window.jspdf;
            const doc = new jsPDF({
                orientation: 'p',
                unit: 'pt',
                format: 'a4'
            });

            const pageMargin = 30; 
            const fullPageWidth = doc.internal.pageSize.getWidth();
            const fullPageHeight = doc.internal.pageSize.getHeight();
            const pageWidth = fullPageWidth - (2 * pageMargin); // Overall content width
            let currentY = pageMargin;
            
            // Define a common target width for image and text content, image will dictate actual
            const commonContentTargetWidth = pageWidth * 0.8; // Aim for 80% of usable page width for content
            const imageMaxHeight = fullPageHeight * 0.45; 
            
            const narrationFontSize = 8; 
            const choiceFontSize = 7;   
            // Increased lineSpacing for more space between text blocks/items
            const lineSpacing = 12; // General spacing after major blocks
            const choiceItemSpacing = lineSpacing * 0.7; // Spacing between choice items, slightly more

            // Paddings (these define space INSIDE the text border)
            const originalTextBlockInternalTopPadding = narrationFontSize * 1.4; 
            const originalTextBlockSidePadding = 6; 
            const originalTextBlockBottomPadding = 6;

            // User requested increase by 10px side, 10px top, 10px bottom for text border area
            const textBlockInternalTopPadding = originalTextBlockInternalTopPadding + 10;
            const textBlockSidePadding = originalTextBlockSidePadding + 10; 
            const textBlockBottomPadding = originalTextBlockBottomPadding + 10;

            const imageBorderColor = [77, 77, 255]; // Purple for image border
            const textBorderColorLightPurple = [170, 170, 255]; // Very light purple for text border
            const defaultTextColor = [230, 230, 230];
            const selectedChoiceColor = [255, 255, 0]; 
            const textBorderThickness = 2;
            const imageFrameThickness = 2; // How much the image frame extends beyond the image

            // Font setup (same as before)
            const fontName = "PressStart2P";
            const fontFileName = "PressStart2P-Regular.ttf";
            const fontUrl = `fonts/${fontFileName}`; 
            let fontSuccessfullyLoaded = false;
            try {
                const fontResponse = await fetch(fontUrl);
                if (!fontResponse.ok) throw new Error(`Font file not found: ${fontUrl}`);
                const fontBlob = await fontResponse.blob();
                const reader = new FileReader();
                await new Promise((resolve, reject) => {
                    reader.onloadend = () => {
                        try {
                            const fontBase64 = reader.result.split(',')[1];
                            doc.addFileToVFS(fontFileName, fontBase64);
                            doc.addFont(fontFileName, fontName, "normal");
                            doc.setFont(fontName);
                            fontSuccessfullyLoaded = true;
                            resolve(true);
                        } catch (e) { reject(e); }
                    };
                    reader.onerror = reject;
                    reader.readAsDataURL(fontBlob);
                });
            } catch (e) {
                console.error("[PDF] Error custom font:", e, "Fallback: Helvetica.");
                doc.setFont("Helvetica", "normal");
            }

            const turnContainers = historyLog.querySelectorAll('.turn-container');
            if (turnContainers.length === 0) {
                alert("No story content to save.");
                connectionStatus.textContent = isConnected ? 'Connected' : 'Disconnected';
                return;
            }

            for (let i = 0; i < turnContainers.length; i++) {
                const turn = turnContainers[i];
                if (i > 0) doc.addPage();
                currentY = pageMargin;
                doc.setFillColor(26, 26, 46); 
                doc.rect(0, 0, fullPageWidth, fullPageHeight, 'F');
                doc.setTextColor(defaultTextColor[0], defaultTextColor[1], defaultTextColor[2]);
                if (fontSuccessfullyLoaded) doc.setFont(fontName, "normal");
                
                let actualContentWidthForTurn = commonContentTargetWidth; // Default if no image
                let contentXOffsetForTurn = pageMargin + (pageWidth - actualContentWidthForTurn) / 2; // Center this default width

                const imgElement = turn.querySelector('.turn-image');
                let imageAddedThisPage = false;

                if (imgElement && imgElement.src && imgElement.src !== PLACEHOLDER_IMG_SRC) {
                    try {
                        if (!imgElement.complete || imgElement.naturalWidth === 0) { // Ensure image is loaded
                            await new Promise((resolve, reject) => { 
                                imgElement.onload = resolve; 
                                imgElement.onerror = reject; 
                                if (imgElement.complete && imgElement.naturalWidth !== 0) resolve();
                            });
                        }
                        const imgData = imgElement.src;
                        const originalWidth = imgElement.naturalWidth || 512;
                        const originalHeight = imgElement.naturalHeight || 512;
                        
                        let imgPdfWidth = originalWidth;
                        let imgPdfHeight = originalHeight;

                        // Scale image to fit commonContentTargetWidth while maintaining aspect ratio
                        if (imgPdfWidth > commonContentTargetWidth) {
                            imgPdfHeight = (commonContentTargetWidth / imgPdfWidth) * imgPdfHeight;
                            imgPdfWidth = commonContentTargetWidth;
                        }
                        // Further scale if height exceeds imageMaxHeight
                        if (imgPdfHeight > imageMaxHeight) {
                            imgPdfWidth = (imageMaxHeight / imgPdfHeight) * imgPdfWidth;
                            imgPdfHeight = imageMaxHeight;
                        }
                        
                        actualContentWidthForTurn = imgPdfWidth; // Image dictates the content width for this turn
                        contentXOffsetForTurn = pageMargin + (pageWidth - actualContentWidthForTurn) / 2; // Re-center based on actual image width

                        // Draw image with purple frame
                        doc.setFillColor(imageBorderColor[0], imageBorderColor[1], imageBorderColor[2]);
                        doc.rect(
                            contentXOffsetForTurn - imageFrameThickness, 
                            currentY - imageFrameThickness, 
                            actualContentWidthForTurn + (2 * imageFrameThickness), 
                            imgPdfHeight + (2 * imageFrameThickness), 
                            'F'
                        );
                        doc.addImage(imgData, 'PNG', contentXOffsetForTurn, currentY, actualContentWidthForTurn, imgPdfHeight);
                        currentY += imgPdfHeight + (2 * imageFrameThickness) + lineSpacing * 1.5; // Extra space after image
                        imageAddedThisPage = true;
                    } catch (e) { 
                        doc.text(`[Image N/A]`, contentXOffsetForTurn + textBlockSidePadding, currentY + textBlockInternalTopPadding);
                        currentY += narrationFontSize + lineSpacing; 
                    }
                } else { 
                    // Minimal space if no image, text will use actualContentWidthForTurn (defaulted to commonContentTargetWidth)
                    // doc.text(`[No image]`, contentXOffsetForTurn + textBlockSidePadding, currentY + textBlockInternalTopPadding); 
                    // currentY += narrationFontSize + lineSpacing; 
                }
                
                doc.setLineHeightFactor(1.5); // Increase line height for all text below

                const narrationElement = turn.querySelector('.turn-narration');
                const choicesElement = turn.querySelector('.turn-choices');
                let combinedTextHeight = 0;
                let splitNarration = [];
                let narrationBlockHeight = 0;
                let allChoicesText = [];
                let actualChoicesContentHeight = 0;

                if (narrationElement && narrationElement.textContent.trim() !== "") {
                    doc.setFontSize(narrationFontSize);
                    let narrationText = narrationElement.innerHTML.replace(/<br\s*\/?>/gi, '\n').replace(/<span class="md-bold">(.+?)<\/span>/gi, '$1').replace(/<[^>]+>/g, '');
                    if (narrationText.trim().toLowerCase() === "escolha seu tema") {
                        narrationText = "Tema Escolhido";
                    }
                    splitNarration = doc.splitTextToSize(narrationText, actualContentWidthForTurn - (textBlockSidePadding * 2));
                    narrationBlockHeight = doc.getTextDimensions(splitNarration).h; // Will be larger due to line height factor
                    if (narrationBlockHeight > 0) combinedTextHeight += narrationBlockHeight;
                }

                const hasChoices = choicesElement && choicesElement.children.length > 0 && choicesElement.children[0].tagName === 'BUTTON';
                if (hasChoices) {
                    doc.setFontSize(choiceFontSize);
                    choicesElement.querySelectorAll('.choice-button').forEach(button => {
                        allChoicesText.push({text: `- ${button.textContent}`, selected: button.classList.contains('selected')});
                    });
                    const choiceLinesForHeightCalc = doc.splitTextToSize(allChoicesText.map(c => c.text).join('\n'), actualContentWidthForTurn - (textBlockSidePadding*2) - 10);
                    actualChoicesContentHeight = doc.getTextDimensions(choiceLinesForHeightCalc).h; // Will be larger
                    if (actualChoicesContentHeight > 0) {
                        if (combinedTextHeight > 0) combinedTextHeight += lineSpacing; // Space between narration and choices
                        combinedTextHeight += actualChoicesContentHeight;
                    }
                }
                
                const totalTextBlockPaddedHeight = combinedTextHeight > 0 ? combinedTextHeight + textBlockInternalTopPadding + textBlockBottomPadding : 0;

                if (combinedTextHeight > 0 && (currentY + totalTextBlockPaddedHeight > fullPageHeight - pageMargin)) {
                    doc.addPage(); 
                    currentY = pageMargin;
                    doc.setFillColor(26, 26, 46); doc.rect(0, 0, fullPageWidth, fullPageHeight, 'F');
                    doc.setTextColor(defaultTextColor[0], defaultTextColor[1], defaultTextColor[2]);
                    if (fontSuccessfullyLoaded) doc.setFont(fontName, "normal"); 
                    imageAddedThisPage = false; 
                }

                if (totalTextBlockPaddedHeight > 0) {
                    doc.setLineWidth(textBorderThickness);
                    doc.setDrawColor(textBorderColorLightPurple[0], textBorderColorLightPurple[1], textBorderColorLightPurple[2]);
                    doc.rect(contentXOffsetForTurn, currentY, actualContentWidthForTurn, totalTextBlockPaddedHeight, 'S');

                    let textY = currentY + textBlockInternalTopPadding; // Text starts after top padding

                    if (splitNarration.length > 0 && narrationBlockHeight > 0) {
                        doc.setFontSize(narrationFontSize);
                        doc.setTextColor(defaultTextColor[0], defaultTextColor[1], defaultTextColor[2]);
                        doc.text(splitNarration, contentXOffsetForTurn + textBlockSidePadding, textY);
                        textY += narrationBlockHeight + lineSpacing; 
                    }

                    if (hasChoices && allChoicesText.length > 0 && actualChoicesContentHeight > 0) {
                        doc.setFontSize(choiceFontSize);
                        allChoicesText.forEach(choiceObj => {
                            const choiceLines = doc.splitTextToSize(choiceObj.text, actualContentWidthForTurn - (textBlockSidePadding*2) - 10);
                            if (choiceObj.selected) {
                                doc.setTextColor(selectedChoiceColor[0], selectedChoiceColor[1], selectedChoiceColor[2]);
                            } else {
                                doc.setTextColor(defaultTextColor[0], defaultTextColor[1], defaultTextColor[2]);
                            }
                            doc.text(choiceLines, contentXOffsetForTurn + textBlockSidePadding + 10, textY); // Indent choices
                            textY += doc.getTextDimensions(choiceLines).h + choiceItemSpacing; // Use choiceItemSpacing
                        });
                    }
                    currentY += totalTextBlockPaddedHeight + lineSpacing * 1.5; // Extra space after text block
                }
                doc.setLineHeightFactor(1.15); // Reset line height factor to jsPDF default for subsequent turns/safety
            }
            doc.save('AurorasJourney.pdf');
            console.log("[PDF] PDF Saved.");
            connectionStatus.textContent = isConnected ? 'Connected' : 'Disconnected';
        } else {
            alert("No story to save yet, or game has not started!");
        }
    }

    // Add event listener for the PDF button
    if (savePdfButton) {
        savePdfButton.addEventListener('click', generatePdf);
    }

    // Initialize WebSocket connection
    connectWebSocket();
}); 