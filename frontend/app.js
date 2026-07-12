document.addEventListener('DOMContentLoaded', () => {
    const copyBtn = document.getElementById('copyBtn');
    const codeBlock = document.getElementById('codeBlock');

    if (copyBtn && codeBlock) {
        copyBtn.addEventListener('click', async () => {
            try {
                // Get the text to copy
                const textToCopy = codeBlock.innerText || codeBlock.textContent;
                
                // Use the modern Clipboard API
                await navigator.clipboard.writeText(textToCopy);
                
                // Visual feedback
                const originalHTML = copyBtn.innerHTML;
                
                // Change icon to a checkmark
                copyBtn.innerHTML = `
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                `;
                copyBtn.classList.add('success');
                
                // Reset after 2 seconds
                setTimeout(() => {
                    copyBtn.innerHTML = originalHTML;
                    copyBtn.classList.remove('success');
                }, 2000);
                
            } catch (err) {
                console.error('Failed to copy text: ', err);
                alert('Failed to copy to clipboard.');
            }
        });
    }
});
