class MayaApp {
    constructor() {
        this.messages = [];
        this.initializeElements();
        this.bindEvents();
        this.addWelcomeMessage();
    }

    initializeElements() {
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
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

    addWelcomeMessage() {
        const welcomeMessage = {
            role: 'assistant',
            content: `Hello! I'm Maya, your UK autism facts assistant. I can help with:

• General information about autism in the UK
• Support services and charities
• Government benefits and rights
• Education and EHCP processes
• Local services in Hounslow

Please ask me any questions about autism support in the UK.`,
            timestamp: new Date().toLocaleString('en-GB')
        };
        this.addMessage(welcomeMessage);
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content) return;

        // Add user message
        this.addMessage({
            role: 'user',
            content: content
        });

        // Clear input and disable button
        this.messageInput.value = '';
        this.sendButton.disabled = true;
        this.sendButton.textContent = 'Thinking...';

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
                content: data.answer,
                timestamp: data.timestamp
            });

        } catch (error) {
            console.error('Chat error:', error);
            this.addMessage({
                role: 'assistant',
                content: `Sorry, I encountered an error: ${error.message}. Please try again.`,
                timestamp: new Date().toLocaleString('en-GB')
            });
        } finally {
            // Re-enable button
            this.sendButton.disabled = false;
            this.sendButton.textContent = 'Send';
            this.messageInput.focus();
        }
    }

    addMessage(message) {
        const messageElement = document.createElement('div');
        messageElement.className = `message message-${message.role}`;
        
        const contentElement = document.createElement('div');
        contentElement.className = 'message-content';
        contentElement.textContent = message.content;
        
        const timestampElement = document.createElement('div');
        timestampElement.className = 'message-timestamp';
        timestampElement.textContent = message.timestamp || new Date().toLocaleString('en-GB');
        
        messageElement.appendChild(contentElement);
        messageElement.appendChild(timestampElement);
        
        this.messagesContainer.appendChild(messageElement);
        this.scrollToBottom();
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
}

// Initialize the app when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new MayaApp();
});