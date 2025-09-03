class ChatApp {
    constructor() {
        this.messages = [];
        this.uploadedFiles = [];
        this.initializeElements();
        this.bindEvents();
    }

    initializeElements() {
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.modelInput = document.getElementById('model');
        this.temperatureInput = document.getElementById('temperature');
        this.fileInput = document.getElementById('fileInput');
        this.attachButton = document.getElementById('attachButton');
        this.uploadedFilesContainer = document.getElementById('uploadedFiles');
    }

    bindEvents() {
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        this.attachButton.addEventListener('click', () => this.fileInput.click());
        this.fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
    }

    async handleFileSelect(event) {
        const files = Array.from(event.target.files);
        
        for (const file of files) {
            try {
                const formData = new FormData();
                formData.append('file', file);
                
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });
                
                if (!response.ok) {
                    throw new Error(`Upload failed: ${response.statusText}`);
                }
                
                const fileData = await response.json();
                this.uploadedFiles.push(fileData);
                this.displayUploadedFile(fileData);
                
            } catch (error) {
                console.error('File upload error:', error);
                alert(`Failed to upload ${file.name}: ${error.message}`);
            }
        }
        
        // Clear the file input
        this.fileInput.value = '';
    }

    displayUploadedFile(fileData) {
        const filePreview = document.createElement('div');
        filePreview.className = 'file-preview';
        
        if (fileData.is_image) {
            const img = document.createElement('img');
            img.src = `data:${fileData.content_type};base64,${fileData.base64_content}`;
            filePreview.appendChild(img);
        }
        
        const fileName = document.createElement('span');
        fileName.textContent = fileData.filename;
        filePreview.appendChild(fileName);
        
        const removeButton = document.createElement('button');
        removeButton.className = 'file-remove';
        removeButton.textContent = '×';
        removeButton.addEventListener('click', () => {
            this.removeUploadedFile(fileData, filePreview);
        });
        filePreview.appendChild(removeButton);
        
        this.uploadedFilesContainer.appendChild(filePreview);
    }

    removeUploadedFile(fileData, previewElement) {
        const index = this.uploadedFiles.indexOf(fileData);
        if (index > -1) {
            this.uploadedFiles.splice(index, 1);
        }
        previewElement.remove();
    }

    async sendMessage() {
        const textContent = this.messageInput.value.trim();
        const hasFiles = this.uploadedFiles.length > 0;
        
        if (!textContent && !hasFiles) return;

        // Disable input while processing
        this.setInputDisabled(true);

        // Prepare message content
        let messageContent;
        let displayContent = textContent;
        
        if (hasFiles) {
            // Create multimodal content
            messageContent = [];
            
            if (textContent) {
                messageContent.push({
                    type: "text",
                    text: textContent
                });
            }
            
            // Add images
            for (const file of this.uploadedFiles) {
                if (file.is_image) {
                    messageContent.push({
                        type: "image_url",
                        image_url: {
                            url: `data:${file.content_type};base64,${file.base64_content}`
                        }
                    });
                }
            }
            
            // Update display content to show files
            const fileNames = this.uploadedFiles.map(f => f.filename).join(', ');
            displayContent = textContent + (textContent ? '\n' : '') + `📎 ${fileNames}`;
        } else {
            messageContent = textContent;
        }

        // Add user message
        this.addMessage('user', displayContent, false, false, this.uploadedFiles.slice());
        this.messageInput.value = '';
        
        // Clear uploaded files
        this.uploadedFiles = [];
        this.uploadedFilesContainer.innerHTML = '';

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

    addMessage(role, content, isThinking = false, isError = false, files = null) {
        // Add to messages array (except thinking messages)
        if (!isThinking) {
            // Use multimodal content if we have files
            if (files && files.length > 0) {
                const multimodalContent = [];
                
                // Add text content
                const textContent = content.split('\n📎')[0].trim();
                if (textContent) {
                    multimodalContent.push({
                        type: "text",
                        text: textContent
                    });
                }
                
                // Add images
                for (const file of files) {
                    if (file.is_image) {
                        multimodalContent.push({
                            type: "image_url",
                            image_url: {
                                url: `data:${file.content_type};base64,${file.base64_content}`
                            }
                        });
                    }
                }
                
                this.messages.push({ role, content: multimodalContent });
            } else {
                this.messages.push({ role, content });
            }
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

        // Handle content with files
        if (files && files.length > 0 && !isThinking) {
            const textPart = content.split('\n📎')[0].trim();
            if (textPart) {
                const textNode = document.createElement('div');
                textNode.textContent = textPart;
                bubbleElement.appendChild(textNode);
            }
            
            // Add image previews
            for (const file of files) {
                if (file.is_image) {
                    const img = document.createElement('img');
                    img.src = `data:${file.content_type};base64,${file.base64_content}`;
                    img.style.maxWidth = '200px';
                    img.style.maxHeight = '200px';
                    img.style.marginTop = '0.5rem';
                    img.style.borderRadius = '0.5rem';
                    bubbleElement.appendChild(img);
                }
            }
        } else {
            bubbleElement.textContent = content;
        }
        
        messageElement.appendChild(bubbleElement);
        this.messagesContainer.appendChild(messageElement);
        this.scrollToBottom();

        return messageElement;
    }

    setInputDisabled(disabled) {
        this.messageInput.disabled = disabled;
        this.sendButton.disabled = disabled;
        this.attachButton.disabled = disabled;
        
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