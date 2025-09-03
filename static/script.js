class ChatApp {
    constructor() {
        this.messages = [];
        this.initializeElements();
        this.bindEvents();
    }

    initializeElements() {
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.modelInput = document.getElementById('model');
        this.temperatureInput = document.getElementById('temperature');
    }

    bindEvents() {
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content) return;

        // Disable input while processing
        this.setInputDisabled(true);

        // Add user message
        this.addMessage('user', content);
        this.messageInput.value = '';

        // Add thinking indicator
        const thinkingElement = this.addMessage('assistant', '...thinking...', true);

        try {
            // Prepare request
            const requestBody = {
                messages: this.messages.map(msg => ({
                    role: msg.role,
                    content: msg.content
                })),
                temperature: parseFloat(this.temperatureInput.value) || 0.7
            };

            // Add model if specified
            const model = this.modelInput.value.trim();
            if (model) {
                requestBody.model = model;
            }

            // Send request
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();

            // Remove thinking indicator and add actual response
            thinkingElement.remove();
            this.addMessage('assistant', data.content);

        } catch (error) {
            // Remove thinking indicator and show error
            thinkingElement.remove();
            this.addMessage('assistant', `Error: ${error.message}`, false, true);
        } finally {
            this.setInputDisabled(false);
            this.messageInput.focus();
        }
    }

    addMessage(role, content, isThinking = false, isError = false) {
        // Add to messages array (except thinking messages)
        if (!isThinking) {
            this.messages.push({ role, content });
        }

        // Create message element
        const messageElement = document.createElement('div');
        messageElement.className = `message ${role}`;

        const bubbleElement = document.createElement('div');
        bubbleElement.className = 'message-bubble';
        
        if (isThinking) {
            bubbleElement.classList.add('thinking');
        }
        
        if (isError) {
            bubbleElement.style.backgroundColor = '#cc3333';
            bubbleElement.style.color = '#fff';
        }

        bubbleElement.textContent = content;
        messageElement.appendChild(bubbleElement);

        this.messagesContainer.appendChild(messageElement);
        this.scrollToBottom();

        return messageElement;
    }

    setInputDisabled(disabled) {
        this.messageInput.disabled = disabled;
        this.sendButton.disabled = disabled;
        
        if (disabled) {
            this.messageInput.placeholder = 'Processing...';
        } else {
            this.messageInput.placeholder = 'Type your message here...';
        }
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
}

// Initialize the chat app when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new ChatApp();
});