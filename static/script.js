class MayaApp {
    constructor() {
        this.messages = [];
        this.lowStimMode = localStorage.getItem('maya-low-stim') === 'true';
        this.initializeElements();
        this.bindEvents();
        this.applyLowStimMode();
        this.addWelcomeMessage();
    }

    initializeElements() {
        this.chatArea = document.getElementById('chatArea');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.lowStimToggle = document.getElementById('lowStimToggle');
        this.suggestions = document.getElementById('suggestions');
    }

    bindEvents() {
        // Send message events
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Low-stimulation mode toggle
        this.lowStimToggle.addEventListener('click', () => {
            this.lowStimMode = !this.lowStimMode;
            localStorage.setItem('maya-low-stim', this.lowStimMode.toString());
            this.applyLowStimMode();
        });

        // Suggestion buttons
        this.suggestions.addEventListener('click', (e) => {
            if (e.target.classList.contains('suggestion-btn')) {
                this.handleSuggestion(e.target.dataset.action);
            }
        });
    }

    applyLowStimMode() {
        if (this.lowStimMode) {
            document.body.classList.add('low-stim');
            this.lowStimToggle.classList.add('active');
            this.lowStimToggle.textContent = 'LOW-STIMULATION MODE';
        } else {
            document.body.classList.remove('low-stim');
            this.lowStimToggle.classList.remove('active');
            this.lowStimToggle.textContent = 'NORMAL MODE';
        }
    }

    addWelcomeMessage() {
        const welcomeMessage = {
            role: 'assistant',
            content: "Hello! I'm here to help autistic people and their carers."
        };
        this.addMessage(welcomeMessage);
    }

    handleSuggestion(action) {
        let message = '';
        switch (action) {
            case 'autism-info':
                message = 'What is autism?';
                break;
            case 'support-services':
                message = 'What support services are available?';
                break;
            case 'benefits':
                message = 'Help me understand autism benefits';
                break;
        }
        
        if (message) {
            this.messageInput.value = message;
            this.sendMessage();
        }
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content) return;

        // Check if this is the first user message
        const isFirstMessage = this.messages.length === 1; // Only welcome message so far
        
        // Hide suggestions after first message
        if (isFirstMessage) {
            this.suggestions.style.display = 'none';
        }

        // Add user message
        this.addMessage({
            role: 'user',
            content: content
        });

        // For first message, show instant initialization notice
        let initMessageElement = null;
        if (isFirstMessage) {
            const initMessage = {
                role: 'assistant',
                content: '**Please wait. Initializing server. This may take up to a minute...**\n\nI\'m loading my knowledge base with information from NHS, National Autistic Society, Gov.UK and other trusted UK sources. Your answer will appear below once ready.'
            };
            this.addMessage(initMessage);
            // Get reference to the message element we just added
            initMessageElement = this.chatArea.lastChild;
        }

        // Clear input and show loading
        this.messageInput.value = '';
        this.setLoading(true);

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ question: content })
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            const data = await response.json();
            
            // If we showed initialization message, remove it first
            if (initMessageElement) {
                initMessageElement.remove();
                this.messages.pop(); // Remove from messages array
            }
            
            // Add assistant response
            this.addMessage({
                role: 'assistant',
                content: data.answer
            });

        } catch (error) {
            console.error('Error sending message:', error);
            
            // Remove initialization message if present
            if (initMessageElement) {
                initMessageElement.remove();
                this.messages.pop();
            }
            
            this.addMessage({
                role: 'assistant',
                content: 'Sorry, I encountered an error. Please try again.'
            });
        } finally {
            this.setLoading(false);
        }
    }

    setLoading(loading) {
        this.sendButton.disabled = loading;
        if (loading) {
            this.sendButton.innerHTML = `
                <div class="loading-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            `;
        } else {
            this.sendButton.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 2L11 13"/>
                    <path d="M22 2L15 22L11 13L2 9L22 2Z"/>
                </svg>
            `;
        }
    }

    addMessage(message) {
        const messageElement = document.createElement('div');
        messageElement.className = `message ${message.role}`;
        
        if (message.role === 'assistant') {
            messageElement.innerHTML = `
                <div class="avatar">M</div>
                <div class="message-bubble">${this.formatMessage(message.content)}</div>
            `;
        } else {
            messageElement.innerHTML = `
                <div class="message-bubble">${this.formatMessage(message.content)}</div>
            `;
        }
        
        this.chatArea.appendChild(messageElement);
        this.scrollToBottom();
        this.messages.push(message);
    }

    formatMessage(content) {
        // Simple formatting - preserve line breaks
        return content.replace(/\n/g, '<br>');
    }

    scrollToBottom() {
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new MayaApp();
});