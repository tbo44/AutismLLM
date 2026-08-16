/* Maya – Autism Hounslow  script.js  v13 */

/* ── Acronym glossary ── */
const ACRONYM_GLOSSARY = {
    'EHCP':   'Education, Health and Care Plan',
    'SEND':   'Special Educational Needs and Disabilities',
    'SEN':    'Special Educational Needs',
    'SENCO':  'Special Educational Needs Co-ordinator',
    'PIP':    'Personal Independence Payment',
    'DLA':    'Disability Living Allowance',
    'ESA':    'Employment and Support Allowance',
    'UC':     'Universal Credit',
    'ASD':    'Autism Spectrum Disorder',
    'ADHD':   'Attention Deficit Hyperactivity Disorder',
    'GP':     'General Practitioner (your family doctor)',
    'CAMHS':  'Child and Adolescent Mental Health Services',
    'OT':     'Occupational Therapist',
    'IPSEA':  'Independent Provider of Special Education Advice',
    'NHS':    'National Health Service',
    'NAS':    'National Autistic Society',
    'TAF':    'Team Around the Family',
    'EHC':    'Education, Health and Care',
    'LA':     'Local Authority',
    'DWP':    'Department for Work and Pensions',
    'SAR':    'Subject Access Request',
    'CCG':    'Clinical Commissioning Group'
};

// Build a single regex from the glossary keys (longest first to avoid partial matches)
const _acronymKeys = Object.keys(ACRONYM_GLOSSARY).sort((a, b) => b.length - a.length);
const _acronymPattern = new RegExp(
    `(<[^>]*>)|(\\b(${_acronymKeys.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b)`,
    'g'
);

class MayaApp {
    constructor() {
        this.messages          = [];
        this.lowStimMode       = localStorage.getItem('maya-low-stim') !== 'false'; // default ON
        this.layoutMode        = localStorage.getItem('maya-layout') || 'focus';
        this.comprehensionLevel = localStorage.getItem('maya-comprehension') || 'standard';
        this.lastQuestion      = '';
        this.lastResponseId    = '';

        this.initElements();
        this.bindEvents();
        this.applyLowStimMode();
        this.applyLayoutMode();
        this.applyComprehensionLevel();
        this.addWelcomeMessage();
    }

    // ── DOM wiring ────────────────────────────────────────────────────

    initElements() {
        this.chatArea           = document.getElementById('chatArea');
        this.messageInput       = document.getElementById('messageInput');
        this.sendButton         = document.getElementById('sendButton');
        this.lowStimToggle      = document.getElementById('lowStimToggle');
        this.layoutToggle       = document.getElementById('layoutToggle');
        this.comprehensionSelect = document.getElementById('comprehensionLevel');
        this.suggestions        = document.getElementById('suggestions');
        this.appContainer       = document.querySelector('.app-container');
        this.infoToggle         = document.getElementById('infoToggle');
        this.infoPanel          = document.getElementById('infoPanel');
        this.reportBtn          = document.getElementById('reportBtn');
        this.privacyBtn         = document.getElementById('privacyBtn');
        this.feedbackOverlay    = document.getElementById('feedbackOverlay');
        this.privacyOverlay     = document.getElementById('privacyOverlay');
        this.feedbackForm       = document.getElementById('feedbackForm');
        this.feedbackThanks     = document.getElementById('feedbackThanks');
        this.closeFeedback      = document.getElementById('closeFeedback');
        this.closeFeedbackDone  = document.getElementById('closeFeedbackDone');
        this.closePrivacy       = document.getElementById('closePrivacy');
    }

    bindEvents() {
        // Send message
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); this.sendMessage(); }
        });

        // Low-stim toggle
        this.lowStimToggle.addEventListener('click', () => {
            this.lowStimMode = !this.lowStimMode;
            localStorage.setItem('maya-low-stim', String(this.lowStimMode));
            this.applyLowStimMode();
        });

        // Layout toggle
        if (this.layoutToggle) {
            this.layoutToggle.addEventListener('click', () => this.toggleLayout());
        }

        // Comprehension level
        this.comprehensionSelect.addEventListener('change', (e) => {
            this.comprehensionLevel = e.target.value;
            localStorage.setItem('maya-comprehension', this.comprehensionLevel);
        });

        // Suggestion buttons
        this.suggestions.addEventListener('click', (e) => {
            const btn = e.target.closest('.suggestion-btn');
            if (btn) this.handleSuggestion(btn.dataset.action);
        });

        // Info panel toggle
        this.infoToggle.addEventListener('click', () => {
            const isHidden = this.infoPanel.hidden;
            this.infoPanel.hidden = !isHidden;
            this.infoToggle.setAttribute('aria-expanded', String(isHidden));
        });

        // Feedback modal
        this.reportBtn.addEventListener('click', () => this.openFeedback());
        this.closeFeedback.addEventListener('click', () => this.closeFeedbackModal());
        this.closeFeedbackDone.addEventListener('click', () => this.closeFeedbackModal());
        this.feedbackForm.addEventListener('submit', (e) => { e.preventDefault(); this.submitFeedback(); });

        // Privacy modal
        this.privacyBtn.addEventListener('click', () => { this.privacyOverlay.hidden = false; });
        this.closePrivacy.addEventListener('click', () => { this.privacyOverlay.hidden = true; });

        // Close modals on overlay click
        this.feedbackOverlay.addEventListener('click', (e) => {
            if (e.target === this.feedbackOverlay) this.closeFeedbackModal();
        });
        this.privacyOverlay.addEventListener('click', (e) => {
            if (e.target === this.privacyOverlay) this.privacyOverlay.hidden = true;
        });

        // Close modals on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeFeedbackModal();
                this.privacyOverlay.hidden = true;
            }
        });
    }

    // ── UI state ──────────────────────────────────────────────────────

    applyLowStimMode() {
        document.body.classList.toggle('low-stim', this.lowStimMode);
        this.lowStimToggle.classList.toggle('active', this.lowStimMode);
        this.lowStimToggle.textContent = this.lowStimMode ? 'Low-stimulation mode' : 'Standard mode';
    }

    toggleLayout() {
        this.layoutMode = this.layoutMode === 'focus' ? 'expanded' : 'focus';
        localStorage.setItem('maya-layout', this.layoutMode);
        this.applyLayoutMode();
    }

    applyLayoutMode() {
        this.appContainer.setAttribute('data-layout', this.layoutMode);
        const expanded = this.layoutMode === 'expanded';
        if (this.layoutToggle) {
            this.layoutToggle.setAttribute('aria-pressed', String(expanded));
            const lbl = this.layoutToggle.querySelector('.layout-label');
            if (lbl) lbl.textContent = expanded ? 'Narrow' : 'Expand';
        }
    }

    applyComprehensionLevel() {
        this.comprehensionSelect.value = this.comprehensionLevel;
    }

    // ── Welcome message ───────────────────────────────────────────────

    addWelcomeMessage() {
        this.addMessage({
            role: 'assistant',
            content: 'Hello! I\'m Maya, the Autism Hounslow information assistant.\n\nI can help with questions about autism, benefits (DLA, PIP, Universal Credit), education (EHCP, SEND), local Hounslow services, and more.\n\nUse the buttons below or type your question.'
        });
    }

    // ── Suggestion buttons ────────────────────────────────────────────

    handleSuggestion(action) {
        const questions = {
            ehcp:     'What is an EHCP and how do I apply for one?',
            pip:      'How do I apply for PIP for someone with autism?',
            local:    'What autism support services are available in Hounslow?',
            dla:      'How do I apply for DLA for my autistic child?'
        };
        const q = questions[action];
        if (q) { this.messageInput.value = q; this.sendMessage(); }
    }

    // ── Send message ──────────────────────────────────────────────────

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content) return;

        const isFirst = this.messages.length === 1;
        if (isFirst) this.suggestions.style.display = 'none';

        this.lastQuestion = content;
        this.lastResponseId = `r-${Date.now()}`;

        this.addMessage({ role: 'user', content });
        this.messageInput.value = '';
        this.setLoading(true);

        // Show initialising notice on first message
        let initEl = null;
        if (isFirst) {
            this.addMessage({
                role: 'assistant',
                content: 'Loading knowledge base… this can take up to a minute on first use. Your answer will appear once ready.'
            });
            initEl = this.chatArea.lastElementChild;
        }

        try {
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question: content,
                    comprehension_level: this.comprehensionLevel
                })
            });

            if (!res.ok) throw new Error(`Server error ${res.status}`);
            const data = await res.json();

            if (initEl) { initEl.remove(); this.messages.pop(); }

            this.addMessage({
                role: 'assistant',
                content: data.answer,
                sources: data.sources || [],
                responseId: this.lastResponseId
            });

        } catch (err) {
            console.error('Chat error:', err);
            if (initEl) { initEl.remove(); this.messages.pop(); }
            this.addMessage({
                role: 'assistant',
                content: 'Sorry, I encountered an error. Please try again.\n\nIf the problem continues, you can find reliable information at:\n• NHS: nhs.uk/conditions/autism\n• National Autistic Society: autism.org.uk\n• Gov.UK SEND: gov.uk/children-with-special-educational-needs'
            });
        } finally {
            this.setLoading(false);
        }
    }

    // ── Loading state ─────────────────────────────────────────────────

    setLoading(on) {
        this.sendButton.disabled = on;
        if (on) {
            this.sendButton.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div>';
        } else {
            this.sendButton.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/></svg>';
        }
    }

    // ── Add message ───────────────────────────────────────────────────

    addMessage(msg) {
        const el = document.createElement('div');
        el.className = `message ${msg.role}`;

        if (msg.role === 'assistant') {
            const sourcesHTML = this.buildSourcesHTML(msg.sources);
            el.innerHTML = `<div class="avatar" aria-hidden="true">M</div><div class="message-bubble">${this.renderAnswer(msg.content, msg)}${sourcesHTML}</div>`;
        } else {
            el.innerHTML = `<div class="message-bubble">${this.escapeHtml(msg.content)}</div>`;
        }

        this.chatArea.appendChild(el);
        this.scrollToBottom();
        this.messages.push(msg);
    }

    // ── Acronym annotation ────────────────────────────────────────────

    /**
     * Wrap the first occurrence of each known acronym in the message HTML
     * with an interactive <abbr> that shows a plain-English tooltip.
     * @param {string} html  - already-rendered HTML
     * @param {Set}    seen  - set of acronyms already annotated in this message
     * @returns {string}
     */
    annotateAcronyms(html, seen) {
        // Reset the regex lastIndex before each use (global flag)
        _acronymPattern.lastIndex = 0;
        return html.replace(_acronymPattern, (match, tag, _full, acronym) => {
            if (tag) return tag;          // inside an HTML tag — skip
            if (!acronym) return match;
            if (seen.has(acronym)) return match;  // already annotated this message
            seen.add(acronym);
            const def = ACRONYM_GLOSSARY[acronym];
            const safedef = def.replace(/"/g, '&quot;');
            return `<abbr class="maya-abbr" tabindex="0" role="term" ` +
                   `data-tooltip="${safedef}" ` +
                   `title="${safedef}" ` +
                   `aria-label="${acronym}: ${safedef}">${acronym}</abbr>`;
        });
    }

    // ── Answer renderer ───────────────────────────────────────────────

    renderAnswer(raw, msg) {
        if (!raw) return '';

        // Per-message set so only the first occurrence of each acronym is annotated
        const seenAcronyms = new Set();

        // Detect structured sections from LLM output
        const sections = this.parseStructuredSections(raw);
        if (sections.length > 0) {
            const html = this.renderStructuredSections(sections, msg);
            return this.annotateAcronyms(html, seenAcronyms);
        }

        // Fallback: plain markdown-style rendering
        const html = `<div class="section-body">${this.renderMarkdown(raw)}</div>`;
        return this.annotateAcronyms(html, seenAcronyms);
    }

    parseStructuredSections(text) {
        // Match ## Section Heading patterns
        const headerRe = /^##\s+(.+)$/gm;
        const matches = [];
        let m;
        while ((m = headerRe.exec(text)) !== null) {
            matches.push({ title: m[1].trim(), index: m.index, end: m.index + m[0].length });
        }
        if (matches.length === 0) return [];

        const sections = [];
        for (let i = 0; i < matches.length; i++) {
            const start = matches[i].end;
            const end   = i + 1 < matches.length ? matches[i + 1].index : text.length;
            const body  = text.slice(start, end).trim();
            sections.push({ title: matches[i].title, body });
        }
        return sections;
    }

    renderStructuredSections(sections, msg) {
        const sectionClass = {
            'Short Answer': 'section-short-answer',
            'Steps':        'section-steps',
            'Who to Contact': 'section-contact',
            'Useful Links': 'section-links',
            'Important Note': 'section-note'
        };

        let html = '';
        let hasSimpler = false;

        for (const s of sections) {
            // Skip the "simpler language" cue line — we render it as a button
            if (/would you like this in simpler language/i.test(s.title) ||
                /would you like this in simpler language/i.test(s.body)) {
                hasSimpler = true;
                continue;
            }

            const cssClass = sectionClass[s.title] || 'section-short-answer';
            const bodyHtml = this.renderMarkdown(s.body);

            html += `<div class="answer-section ${cssClass}"><span class="section-heading">${this.escapeHtml(s.title)}</span><div class="section-body">${bodyHtml}</div></div>`;
        }

        // Also check the raw text for the "simpler" cue line (after the last section)
        if (!hasSimpler && /would you like this in simpler language/i.test(msg.content)) {
            hasSimpler = true;
        }

        if (hasSimpler && this.comprehensionLevel !== 'clear') {
            html += `<button class="simpler-btn" type="button" data-action="simpler">Simpler language ↩</button>`;
        }

        return html;
    }

    // ── Markdown-style renderer ───────────────────────────────────────

    renderMarkdown(text) {
        if (!text) return '';

        // Escape HTML first
        let out = this.escapeHtml(text);

        // Bold: **text**
        out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic: *text* or _text_ (single)
        out = out.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

        // Links: [text](url)
        out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

        // Numbered lists
        const lines = out.split('\n');
        const result = [];
        let inOl = false;
        let inUl = false;

        for (const line of lines) {
            const olMatch = line.match(/^(\d+)\.\s+(.+)$/);
            const ulMatch = line.match(/^[-•]\s+(.+)$/);

            if (olMatch) {
                if (!inOl) { if (inUl) { result.push('</ul>'); inUl = false; } result.push('<ol>'); inOl = true; }
                result.push(`<li>${olMatch[2]}</li>`);
            } else if (ulMatch) {
                if (!inUl) { if (inOl) { result.push('</ol>'); inOl = false; } result.push('<ul>'); inUl = true; }
                result.push(`<li>${ulMatch[1]}</li>`);
            } else {
                if (inOl) { result.push('</ol>'); inOl = false; }
                if (inUl) { result.push('</ul>'); inUl = false; }
                result.push(line === '' ? '' : line);
            }
        }

        if (inOl) result.push('</ol>');
        if (inUl) result.push('</ul>');

        // Join lines; collapse 3+ blank lines to 2
        out = result.join('\n').replace(/\n{3,}/g, '\n\n');

        // Convert newlines to <br> outside of list items
        out = out.replace(/\n/g, '<br>');

        // Fix <br> inside / between list elements
        out = out.replace(/<br>\s*(<\/?[uo]l>|<li>)/g, '$1');
        out = out.replace(/<\/li><br>/g, '</li>');

        return out;
    }

    escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ── Sources section ───────────────────────────────────────────────

    buildSourcesHTML(sources) {
        if (!sources || sources.length === 0) return '';
        const items = sources.map(s => `<li><a href="${this.escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${s.publisher ? `<strong>${this.escapeHtml(s.publisher)}</strong> – ` : ''}${this.escapeHtml(s.title)}</a></li>`).join('');
        return `<details class="sources"><summary>Sources &amp; disclaimer</summary><ul class="sources-list">${items}</ul><div class="disclaimer-expanded">This response was generated by Maya using the sources listed above. Always verify important information with the original source before acting on it.<br><strong>Medical questions:</strong> contact your GP or call NHS 111.<br><strong>SEND/legal issues:</strong> contact IPSEA or Citizens Advice.<br><strong>In a crisis:</strong> call 999 or Samaritans 116 123.</div></details>`;
    }

    // ── Simpler language button (event delegation) ────────────────────

    handleSimplerBtn(question) {
        this.messageInput.value = question;
        const prev = this.comprehensionLevel;
        this.comprehensionLevel = 'clear';
        this.comprehensionSelect.value = 'clear';
        localStorage.setItem('maya-comprehension', 'clear');
        this.sendMessage().finally(() => {
            // Keep 'clear' selected after request
        });
        void prev;
    }

    // ── Feedback ──────────────────────────────────────────────────────

    openFeedback() {
        this.feedbackForm.hidden       = false;
        this.feedbackThanks.hidden     = true;
        document.getElementById('issueType').value     = '';
        document.getElementById('feedbackComment').value = '';
        this.feedbackOverlay.hidden = false;
    }

    closeFeedbackModal() {
        this.feedbackOverlay.hidden = true;
    }

    async submitFeedback() {
        const issueType = document.getElementById('issueType').value;
        const comment   = document.getElementById('feedbackComment').value;
        if (!issueType) return;

        try {
            await fetch('/feedback', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question:    this.lastQuestion,
                    response_id: this.lastResponseId,
                    issue_type:  issueType,
                    comment:     comment
                })
            });
        } catch (e) {
            console.warn('Feedback submit error:', e);
        }

        this.feedbackForm.hidden   = true;
        this.feedbackThanks.hidden = false;
    }

    // ── Scroll ────────────────────────────────────────────────────────

    scrollToBottom() {
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }
}

// ── Boot ──────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    const app = new MayaApp();

    // Global click handler for "simpler language" buttons (event delegation)
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.simpler-btn[data-action="simpler"]');
        if (btn) {
            e.preventDefault();
            app.handleSimplerBtn(app.lastQuestion);
        }
    });

    // ── Acronym tooltip: tap/click to open, keyboard Enter/Space ──────
    document.addEventListener('click', (e) => {
        const abbr = e.target.closest('.maya-abbr');
        if (abbr) {
            // Toggle open on this one, close all others
            const wasOpen = abbr.classList.contains('open');
            document.querySelectorAll('.maya-abbr.open').forEach(el => el.classList.remove('open'));
            if (!wasOpen) abbr.classList.add('open');
            e.stopPropagation();
            return;
        }
        // Click outside → close any open tooltips
        document.querySelectorAll('.maya-abbr.open').forEach(el => el.classList.remove('open'));
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            const abbr = e.target.closest('.maya-abbr');
            if (abbr) {
                e.preventDefault();
                const wasOpen = abbr.classList.contains('open');
                document.querySelectorAll('.maya-abbr.open').forEach(el => el.classList.remove('open'));
                if (!wasOpen) abbr.classList.add('open');
            }
        }
        if (e.key === 'Escape') {
            document.querySelectorAll('.maya-abbr.open').forEach(el => el.classList.remove('open'));
        }
    });
});
