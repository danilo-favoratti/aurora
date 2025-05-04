document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const narrationElement = document.getElementById('narration');
    const choicesContainer = document.getElementById('choices-container');
    const sceneImage = document.getElementById('scene-image');
    const connectionStatus = document.getElementById('connection-status');
    
    // Generate a random session ID
    const sessionId = Math.random().toString(36).substring(2, 15);
    
    // WebSocket connection
    let socket;
    let isConnected = false;
    let narrationComplete = false;
    let cursorElement = null;
    
    // Connect to WebSocket
    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${sessionId}`;
        
        socket = new WebSocket(wsUrl);
        
        // WebSocket event handlers
        socket.onopen = () => {
            isConnected = true;
            connectionStatus.textContent = 'Connected';
            connectionStatus.style.color = '#4dff4d'; // Green
        };
        
        socket.onclose = () => {
            isConnected = false;
            connectionStatus.textContent = 'Disconnected. Reconnecting...';
            connectionStatus.style.color = '#ff4d4d'; // Red
            
            // Try to reconnect
            setTimeout(connectWebSocket, 3000);
        };
        
        socket.onerror = (error) => {
            console.error('WebSocket Error:', error);
            connectionStatus.textContent = 'Error connecting';
            connectionStatus.style.color = '#ff4d4d'; // Red
        };
        
        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            // Handle different message types
            switch (data.type) {
                case 'text':
                    handleTextMessage(data.content);
                    break;
                    
                case 'image':
                    handleImageMessage(data.content);
                    break;
                    
                case 'choices':
                    handleChoicesMessage(data.content);
                    break;
                    
                case 'error':
                    handleErrorMessage(data.content);
                    break;
                    
                default:
                    console.warn('Unknown message type:', data.type);
            }
        };
    }
    
    // Handle text messages (tokens)
    function handleTextMessage(token) {
        // Create cursor element if it doesn't exist
        if (!cursorElement) {
            cursorElement = document.createElement('span');
            cursorElement.className = 'cursor';
            narrationElement.appendChild(cursorElement);
        }
        
        // Insert token before cursor
        const textNode = document.createTextNode(token);
        narrationElement.insertBefore(textNode, cursorElement);
        
        // Auto-scroll to bottom
        narrationElement.scrollTop = narrationElement.scrollHeight;
    }
    
    // Handle image messages
    function handleImageMessage(base64Image) {
        sceneImage.src = `data:image/png;base64,${base64Image}`;
    }
    
    // Handle choices messages
    function handleChoicesMessage(choices) {
        // Clear previous choices
        choicesContainer.innerHTML = '';
        
        // Add new choices
        choices.forEach(choice => {
            const button = document.createElement('button');
            button.className = 'choice-button';
            button.textContent = choice;
            
            button.addEventListener('click', () => {
                // Send the choice to the server
                if (isConnected) {
                    socket.send(JSON.stringify({ choice }));
                    
                    // Clear choices once one is selected
                    choicesContainer.innerHTML = '';
                    
                    // Reset for next narration
                    narrationElement.innerHTML = '';
                    cursorElement = null;
                }
            });
            
            choicesContainer.appendChild(button);
        });
    }
    
    // Handle error messages
    function handleErrorMessage(errorMsg) {
        console.error('Server error:', errorMsg);
        
        // Display error in narration
        const errorElement = document.createElement('div');
        errorElement.className = 'error-message';
        errorElement.textContent = errorMsg;
        narrationElement.appendChild(errorElement);
    }
    
    // Set a default placeholder image
    sceneImage.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
    
    // Initialize WebSocket connection
    connectWebSocket();
}); 