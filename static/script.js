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
            case 'tell-joke':
                message = 'Tell me a joke';
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

        // Hide suggestions after first message
        if (this.messages.length === 1) { // Only welcome message so far
            this.suggestions.style.display = 'none';
        }

        // Add user message
        this.addMessage({
            role: 'user',
            content: content
        });

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
            
            // Add assistant response
            this.addMessage({
                role: 'assistant',
                content: data.answer
            });

        } catch (error) {
            console.error('Error sending message:', error);
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