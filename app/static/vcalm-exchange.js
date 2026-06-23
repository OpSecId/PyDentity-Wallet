let vcalmConsentResolver = null;

function truncateDid(did, head = 16, tail = 8) {
    if (!did || did.length <= head + tail + 3) return did || '';
    return `${did.slice(0, head)}...${did.slice(-tail)}`;
}

function renderCredentialPreview(credential) {
    const attrs = Object.entries(credential.attributes || {})
        .slice(0, 6)
        .map(([key, value]) => `
            <div class="col-6">
                <small class="text-muted d-block">${key}</small>
                <div class="fw-bold text-truncate">${value}</div>
            </div>
        `)
        .join('');

    return `
        <div class="card card-sm mb-3">
            <div class="card-body">
                <div class="fw-bold mb-1">${credential.name || 'Credential'}</div>
                <div class="text-muted small mb-3">${credential.issuer || 'Unknown issuer'}</div>
                ${attrs ? `<div class="row g-2">${attrs}</div>` : ''}
            </div>
        </div>
    `;
}

function populateConsentModal(preview) {
    const title = document.getElementById('vcalm-consent-title');
    const detail = document.getElementById('vcalm-consent-detail');
    const presentation = document.getElementById('vcalm-consent-presentation');
    const store = document.getElementById('vcalm-consent-store');
    const confirm = document.getElementById('vcalm-consent-confirm');
    const decline = document.getElementById('vcalm-consent-decline');
    const processing = document.getElementById('vcalm-consent-processing');
    const actions = document.getElementById('vcalm-consent-actions');

    processing.classList.add('d-none');
    actions.classList.remove('d-none');
    title.textContent = preview.title || 'Confirm';
    detail.textContent = preview.detail || '';
    confirm.textContent = preview.confirmLabel || 'Continue';
    decline.textContent = preview.declineLabel || 'Cancel';

    if (preview.consentType === 'presentation') {
        presentation.classList.remove('d-none');
        store.classList.add('d-none');
        document.getElementById('vcalm-consent-domain').textContent = preview.domain || 'Unknown site';
        document.getElementById('vcalm-consent-holder').textContent = truncateDid(preview.holderDid);

        const reasons = document.getElementById('vcalm-consent-reasons');
        if (preview.reasons && preview.reasons.length) {
            reasons.innerHTML = preview.reasons.map((reason) => `<div class="alert alert-info py-2 mb-2">${reason}</div>`).join('');
        } else {
            reasons.innerHTML = '';
        }

        const sharedWrap = document.getElementById('vcalm-consent-shared');
        const sharedList = document.getElementById('vcalm-consent-shared-list');
        if (preview.sharedCredentials && preview.sharedCredentials.length) {
            sharedWrap.classList.remove('d-none');
            sharedList.innerHTML = preview.sharedCredentials.map(renderCredentialPreview).join('');
        } else {
            sharedWrap.classList.add('d-none');
            sharedList.innerHTML = '';
        }
    } else {
        presentation.classList.add('d-none');
        store.classList.remove('d-none');
        const credentials = document.getElementById('vcalm-consent-credentials');
        credentials.innerHTML = (preview.credentials || []).map(renderCredentialPreview).join('');
    }
}

function showConsentProcessing() {
    document.getElementById('vcalm-consent-processing').classList.remove('d-none');
    document.getElementById('vcalm-consent-actions').classList.add('d-none');
    document.getElementById('vcalm-consent-presentation').classList.add('d-none');
    document.getElementById('vcalm-consent-store').classList.add('d-none');
}

function showVcalmConsentModal(preview) {
    return new Promise((resolve) => {
        const modalEl = document.getElementById('vcalm-consent-modal');
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        const confirm = document.getElementById('vcalm-consent-confirm');
        const decline = document.getElementById('vcalm-consent-decline');

        populateConsentModal(preview);
        vcalmConsentResolver = resolve;

        const cleanup = () => {
            confirm.removeEventListener('click', onConfirm);
            decline.removeEventListener('click', onDecline);
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            vcalmConsentResolver = null;
        };

        const onConfirm = () => {
            cleanup();
            showConsentProcessing();
            resolve(true);
        };

        const onDecline = () => {
            cleanup();
            modal.hide();
            resolve(false);
        };

        const onHidden = () => {
            if (vcalmConsentResolver) {
                cleanup();
                resolve(false);
            }
        };

        confirm.addEventListener('click', onConfirm);
        decline.addEventListener('click', onDecline);
        modalEl.addEventListener('hidden.bs.modal', onHidden);
        modal.show();
    });
}

function hideVcalmConsentModal() {
    const modalEl = document.getElementById('vcalm-consent-modal');
    const modal = bootstrap.Modal.getInstance(modalEl);
    if (modal) {
        modal.hide();
    }
}

async function postExchangeAction(exchangeId, action) {
    const response = await fetch(`/exchange/${exchangeId}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || data.result?.message || 'Exchange request failed');
    }
    return data.result;
}

async function continueVcalmExchange(result) {
    let current = result;

    while (current.status === 'awaiting_consent') {
        const approved = await showVcalmConsentModal(current.preview);
        hideVcalmConsentModal();

        if (!approved) {
            const action = current.consentType === 'store'
                ? 'decline-store'
                : 'decline-presentation';
            current = await postExchangeAction(current.exchangeId, action);
            continue;
        }

        const action = current.consentType === 'store' ? 'store' : 'present';
        current = await postExchangeAction(current.exchangeId, action);
    }

    return current;
}

async function handleVcalmResult(scanResult) {
    try {
        const result = await continueVcalmExchange(scanResult);

        if (result.status === 'abandoned') {
            alert('Request cancelled.');
            return;
        }

        if (result.status === 'error') {
            alert(result.message || 'VCALM exchange failed');
            return;
        }

        if (['complete', 'redirect'].includes(result.status)) {
            showExchangeComplete(result);
            return;
        }

        alert('Unexpected exchange state. Please try again.');
    } catch (error) {
        console.error('VCALM exchange error:', error);
        hideVcalmConsentModal();
        alert(error.message || 'VCALM exchange failed');
    }
}
