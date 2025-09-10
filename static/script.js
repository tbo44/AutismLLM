// ===== PREFERENCES MANAGEMENT =====
class PreferencesManager {
    constructor() {
        this.defaultPrefs = {
            themeMode: 'colorful',
            highContrast: false,
            reduceMotion: false,
            textSize: 'medium',
            showEmojis: true,
            carerMode: false
        };
        this.preferences = this.loadPreferences();
        this.applyPreferences();
    }

    loadPreferences() {
        try {
            const stored = localStorage.getItem('maya-preferences');
            return stored ? { ...this.defaultPrefs, ...JSON.parse(stored) } : this.defaultPrefs;
        } catch (error) {
            console.warn('Failed to load preferences:', error);
            return this.defaultPrefs;
        }
    }

    savePreferences() {
        try {
            localStorage.setItem('maya-preferences', JSON.stringify(this.preferences));
        } catch (error) {
            console.warn('Failed to save preferences:', error);
        }
    }

    updatePreference(key, value) {
        this.preferences[key] = value;
        this.savePreferences();
        this.applyPreferences();
        this.showSuccessToast(`${key} updated`);
    }

    applyPreferences() {
        const root = document.documentElement;
        
        // Apply theme mode
        if (this.preferences.themeMode === 'low-stim') {
            root.setAttribute('data-theme', 'low-stim');
        } else if (this.preferences.highContrast) {
            root.setAttribute('data-theme', 'high-contrast');
        } else {
            root.removeAttribute('data-theme');
        }

        // Apply text size
        root.setAttribute('data-text-size', this.preferences.textSize);
        
        // Apply reduce motion
        root.setAttribute('data-reduce-motion', this.preferences.reduceMotion.toString());
        
        // Apply emoji visibility
        root.setAttribute('data-show-emojis', this.preferences.showEmojis.toString());

        // Update mode chip
        const modeChip = document.getElementById('currentModeChip');
        if (modeChip) {
            modeChip.textContent = this.preferences.themeMode === 'low-stim' ? 'Low-stimulation' :
                                   this.preferences.highContrast ? 'High-contrast' : 'Colorful';
        }
    }

    showSuccessToast(message) {
        const toast = document.getElementById('successToast');
        const messageElement = toast.querySelector('.toast-message');
        messageElement.textContent = message;
        toast.style.display = 'flex';
        
        setTimeout(() => {
            toast.style.display = 'none';
        }, 3000);
    }
}

// ===== ENHANCED MAYA APP =====
class MayaApp {
    constructor() {
        this.messages = [];
        this.preferences = new PreferencesManager();
        this.initializeElements();
        this.bindEvents();
        this.setupAccessibility();
        this.addWelcomeMessage();
    }

    initializeElements() {
        // Core elements
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        
        // New elements
        this.settingsButton = document.getElementById('settingsButton');
        this.settingsModal = document.getElementById('settingsModal');
        this.closeSettingsButton = document.getElementById('closeSettings');
        this.quickActions = document.getElementById('quickActions');
        this.carerModeToggle = document.getElementById('carerModeToggle');
        
        // Settings controls
        this.lowStimToggle = document.getElementById('lowStimToggle');
        this.highContrastToggle = document.getElementById('highContrastToggle');
        this.reduceMotionToggle = document.getElementById('reduceMotionToggle');
        this.showEmojisToggle = document.getElementById('showEmojisToggle');
        this.textSizeRadios = document.querySelectorAll('input[name="textSize"]');
    }

    bindEvents() {
        // Chat functionality
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Settings modal
        this.settingsButton.addEventListener('click', () => this.openSettings());
        this.closeSettingsButton.addEventListener('click', () => this.closeSettings());
        this.settingsModal.addEventListener('click', (e) => {
            if (e.target === this.settingsModal) {
                this.closeSettings();
            }
        });

        // Quick actions
        this.quickActions.addEventListener('click', (e) => {
            if (e.target.classList.contains('quick-action-btn')) {
                this.handleQuickAction(e.target.dataset.action);
            }
        });

        // Carer mode toggle
        this.carerModeToggle.addEventListener('change', (e) => {
            this.preferences.updatePreference('carerMode', e.target.checked);
        });

        // Settings toggles
        this.lowStimToggle.addEventListener('change', (e) => {
            if (e.target.checked) {
                this.preferences.updatePreference('themeMode', 'low-stim');
                this.highContrastToggle.checked = false;
            } else {
                this.preferences.updatePreference('themeMode', 'colorful');
            }
        });

        this.highContrastToggle.addEventListener('change', (e) => {
            this.preferences.updatePreference('highContrast', e.target.checked);
            if (e.target.checked) {
                this.lowStimToggle.checked = false;
                this.preferences.updatePreference('themeMode', 'colorful');
            }
        });

        this.reduceMotionToggle.addEventListener('change', (e) => {
            this.preferences.updatePreference('reduceMotion', e.target.checked);
        });

        this.showEmojisToggle.addEventListener('change', (e) => {
            this.preferences.updatePreference('showEmojis', e.target.checked);
        });

        // Text size controls
        this.textSizeRadios.forEach(radio => {
            radio.addEventListener('change', (e) => {
                if (e.target.checked) {
                    this.preferences.updatePreference('textSize', e.target.value);
                }
            });
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.settingsModal.style.display !== 'none') {
                this.closeSettings();
            }
        });
    }

    setupAccessibility() {
        // Set initial values based on preferences
        this.carerModeToggle.checked = this.preferences.preferences.carerMode;
        this.lowStimToggle.checked = this.preferences.preferences.themeMode === 'low-stim';
        this.highContrastToggle.checked = this.preferences.preferences.highContrast;
        this.reduceMotionToggle.checked = this.preferences.preferences.reduceMotion;
        this.showEmojisToggle.checked = this.preferences.preferences.showEmojis;
        
        // Set text size radio
        const textSizeRadio = document.querySelector(`input[name="textSize"][value="${this.preferences.preferences.textSize}"]`);
        if (textSizeRadio) {
            textSizeRadio.checked = true;
        }

        // Add keyboard navigation support
        this.setupKeyboardNavigation();
    }

    setupKeyboardNavigation() {
        // Trap focus within modal when open
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Tab' && this.settingsModal.style.display !== 'none') {
                this.trapFocus(e);
            }
        });
    }

    trapFocus(e) {
        const focusableElements = this.settingsModal.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (e.shiftKey && document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
        } else if (!e.shiftKey && document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
        }
    }

    openSettings() {
        this.settingsModal.style.display = 'flex';
        this.closeSettingsButton.focus();
    }

    closeSettings() {
        this.settingsModal.style.display = 'none';
        this.settingsButton.focus();
    }

    handleQuickAction(action) {
        const currentMessage = this.messageInput.value;
        let enhancedMessage = currentMessage;

        switch (action) {
            case 'step-by-step':
                enhancedMessage = `Please explain this step-by-step: ${currentMessage}`;
                break;
            case 'shorter':
                enhancedMessage = `Please give a short answer: ${currentMessage}`;
                break;
            case 'sources':
                enhancedMessage = `Please show sources for: ${currentMessage}`;
                break;
        }

        this.messageInput.value = enhancedMessage;
        this.messageInput.focus();
    }

    addWelcomeMessage() {
        const emoji = this.preferences.preferences.showEmojis;
        const welcomeContent = `Hello! I'm Maya, your UK autism facts assistant. I can help with:

${emoji ? '•' : '-'} General information about autism in the UK
${emoji ? '•' : '-'} Support services and charities  
${emoji ? '•' : '-'} Government benefits and rights
${emoji ? '•' : '-'} Education and EHCP processes
${emoji ? '•' : '-'} Local services in Hounslow

Please ask me any questions about autism support in the UK.`;

        const welcomeMessage = {
            role: 'assistant',
            content: welcomeContent,
            timestamp: new Date().toLocaleString('en-GB')
        };
        
        this.addMessage(welcomeMessage);
        this.showQuickActions();
    }

    showQuickActions() {
        this.quickActions.style.display = 'flex';
    }

    hideQuickActions() {
        this.quickActions.style.display = 'none';
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content) return;

        // Hide quick actions during conversation
        this.hideQuickActions();

        // Add user message
        this.addMessage({
            role: 'user',
            content: content
        });

        // Clear input and show loading state
        this.messageInput.value = '';
        this.sendButton.disabled = true;
        this.sendButton.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="animate-spin">
                <path d="M21 12a9 9 0 11-6.219-8.56"/>
            </svg>
        `;

        try {
            // Modify request based on carer mode
            let requestBody = { question: content };
            if (this.preferences.preferences.carerMode) {
                requestBody.carer_mode = true;
            }

            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            const data = await response.json();
            
            // Add assistant response
            this.addMessage({
                role: 'assistant',
                content: data.answer,
                timestamp: data.timestamp || new Date().toLocaleString('en-GB')
            });

        } catch (error) {
            console.error('Error sending message:', error);
            this.addMessage({
                role: 'assistant',
                content: `${this.preferences.preferences.showEmojis ? '⚠️' : ''} Sorry, I'm having trouble connecting. Please try again in a moment.`,
                timestamp: new Date().toLocaleString('en-GB')
            });
        } finally {
            // Reset send button
            this.sendButton.disabled = false;
            this.sendButton.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22,2 15,22 11,13 2,9 22,2"></polygon>
                </svg>
            `;
            this.messageInput.focus();
            
            // Show quick actions after response
            setTimeout(() => this.showQuickActions(), 1000);
        }
    }

    addMessage(message) {
        const messageElement = document.createElement('div');
        messageElement.className = `message message-${message.role}`;
        messageElement.setAttribute('role', message.role === 'assistant' ? 'log' : 'log');
        messageElement.setAttribute('aria-live', message.role === 'assistant' ? 'polite' : 'off');
        
        const contentElement = document.createElement('div');
        contentElement.className = 'message-content';
        contentElement.textContent = message.content;
        
        if (message.timestamp) {
            const timestampElement = document.createElement('div');
            timestampElement.className = 'message-timestamp';
            timestampElement.textContent = message.timestamp;
            contentElement.appendChild(timestampElement);
        }
        
        messageElement.appendChild(contentElement);
        this.messagesContainer.appendChild(messageElement);
        this.scrollToBottom();

        // Store message
        this.messages.push(message);
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
}

// ===== INITIALIZE APP =====
document.addEventListener('DOMContentLoaded', () => {
    // Add skip to content link
    const skipLink = document.createElement('a');
    skipLink.href = '#messages';
    skipLink.className = 'skip-to-content';
    skipLink.textContent = 'Skip to chat content';
    document.body.insertBefore(skipLink, document.body.firstChild);

    // Add landmark roles
    const header = document.querySelector('.header-bar');
    const main = document.querySelector('.chat-container');
    if (header) header.setAttribute('role', 'banner');
    if (main) main.setAttribute('role', 'main');

    // Initialize the app
    new MayaApp();
});

// ===== SERVICE WORKER FOR OFFLINE SUPPORT (Optional Enhancement) =====
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        // Register service worker for offline support if needed
        // navigator.serviceWorker.register('/sw.js').catch(console.error);
    });
}