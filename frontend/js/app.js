/**
 * Trading Journal — Main Application Logic
 */

// ---- State ----
let currentPage = 'overview';
let tradesState = { page: 1, pageSize: 50, orderBy: 'timestamp', orderDir: 'desc' };

// ---- Navigation ----
function navigateTo(page) {
    currentPage = page;

    // Update nav links
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.toggle('active', link.dataset.page === page);
    });

    // Show/hide pages
    document.querySelectorAll('.page').forEach(p => {
        p.classList.toggle('active', p.id === `page-${page}`);
    });

    // Load page data
    loadPageData(page);
}

function loadPageData(page) {
    switch (page) {
        case 'overview':  loadOverview(); break;
        case 'trades':    loadTrades();   break;
        case 'exchanges': loadExchanges(); break;
        case 'settings':  loadSettings();  break;
        // pnl: Phase 3
    }
}

// ---- Overview ----
async function loadOverview() {
    try {
        const data = await API.overview();
        setText('stat-total-pnl',     formatCurrency(data.total_pnl));
        setText('stat-pnl-month',     formatCurrency(data.pnl_this_month));
        setText('stat-pnl-week',      formatCurrency(data.pnl_this_week));
        setText('stat-win-rate',      data.win_rate > 0 ? `${data.win_rate}%` : '—');
        setText('stat-total-fees',    formatCurrency(data.total_fees));
        setText('stat-exchanges',     data.connected_exchanges);
        setText('stat-best-pair',     data.best_pair || '—');
        setText('stat-worst-pair',    data.worst_pair || '—');
        setText('stat-trades-month',  `${data.trades_this_month} trades`);
        setText('stat-trades-week',   `${data.trades_this_week} trades`);
        setText('stat-total-trades',  `${data.total_trades} total trades`);

        // Colour PnL values
        colourPnl('stat-total-pnl', data.total_pnl);
        colourPnl('stat-pnl-month', data.pnl_this_month);
        colourPnl('stat-pnl-week',  data.pnl_this_week);

        // Show/hide empty state
        const empty = document.getElementById('overview-empty');
        if (empty) empty.style.display = data.total_trades === 0 ? 'block' : 'none';
    } catch (err) {
        console.error('Failed to load overview:', err);
    }
}

// ---- Trades ----
async function loadTrades() {
    try {
        const filters = getTradeFilters();
        const params = {
            page: tradesState.page,
            page_size: tradesState.pageSize,
            order_by: tradesState.orderBy,
            order_dir: tradesState.orderDir,
            ...filters,
        };

        const data = await API.trades(params);
        renderTradesTable(data.trades);
        renderPagination(data);

        // Also load filter options
        loadFilterOptions();
    } catch (err) {
        console.error('Failed to load trades:', err);
    }
}

function getTradeFilters() {
    return {
        exchange:  document.getElementById('filter-exchange')?.value || '',
        pair:      document.getElementById('filter-pair')?.value || '',
        side:      document.getElementById('filter-side')?.value || '',
        strategy:  document.getElementById('filter-strategy')?.value || '',
        date_from: document.getElementById('filter-date-from')?.value || '',
        date_to:   document.getElementById('filter-date-to')?.value || '',
    };
}

async function loadFilterOptions() {
    try {
        // Load pairs for dropdown
        const pairs = await API.pairs();
        const pairSelect = document.getElementById('filter-pair');
        if (pairSelect && pairSelect.options.length <= 1) {
            pairs.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = p;
                pairSelect.appendChild(opt);
            });
        }

        // Load exchanges for dropdown
        const exchanges = await API.exchanges();
        const exSelect = document.getElementById('filter-exchange');
        if (exSelect && exSelect.options.length <= 1) {
            exchanges.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.exchange;
                opt.textContent = e.label || e.exchange;
                exSelect.appendChild(opt);
            });
        }

        // Load strategies
        const strategies = await API.strategies();
        const stratSelect = document.getElementById('filter-strategy');
        if (stratSelect && stratSelect.options.length <= 2) {
            strategies.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.strategy;
                opt.textContent = s.strategy;
                stratSelect.appendChild(opt);
            });
        }
    } catch (err) {
        // Non-critical — filters just won't populate
    }
}

function renderTradesTable(trades) {
    const tbody = document.getElementById('trades-tbody');
    if (!tbody) return;

    if (trades.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="10">
            <div class="table-empty">No trades found matching your filters.</div>
        </td></tr>`;
        return;
    }

    tbody.innerHTML = trades.map(t => {
        const hasMultipleFills = t.fill_count > 1;
        const expandBtn = hasMultipleFills
            ? `<span class="fill-toggle" onclick="toggleFills('${t.order_id || t.id}', this)" title="Click to expand fills">▶ ${t.fill_count}</span>`
            : `<span style="color:var(--text-muted)">1</span>`;

        return `
        <tr data-id="${t.id}" class="${hasMultipleFills ? 'expandable' : ''}">
            <td class="mono">${formatTime(t.timestamp)}</td>
            <td>${capitalise(t.exchange)}</td>
            <td><strong>${t.pair}</strong></td>
            <td class="side-${t.side}">${t.side.toUpperCase()}</td>
            <td class="mono">${formatQty(t.quantity)}</td>
            <td class="mono">${formatPrice(t.price)}</td>
            <td class="mono">${formatCurrency(t.total)}</td>
            <td class="mono">${formatFee(t.fee, t.fee_currency)}</td>
            <td>${t.strategy ? `<span class="phase-badge">${t.strategy}</span>` : '<span style="color:var(--text-muted)">—</span>'}</td>
            <td class="fills-col">${expandBtn}</td>
        </tr>`;
    }).join('');
}

async function toggleFills(orderId, toggleEl) {
    const parentRow = toggleEl.closest('tr');
    const existingFills = parentRow.parentNode.querySelectorAll(`.fill-row[data-order="${orderId}"]`);

    // If fills are already shown, collapse them
    if (existingFills.length > 0) {
        existingFills.forEach(r => r.remove());
        toggleEl.textContent = `▶ ${toggleEl.textContent.replace('▼ ', '').replace('▶ ', '')}`;
        return;
    }

    // Fetch fills from the API
    try {
        const fills = await API.fills(orderId);
        const fillRows = fills.map(f => `
            <tr class="fill-row" data-order="${orderId}">
                <td class="mono fill-indent">${formatTime(f.timestamp)}</td>
                <td style="color:var(--text-muted)">↳ fill</td>
                <td>${f.pair}</td>
                <td class="side-${f.side}">${f.side.toUpperCase()}</td>
                <td class="mono">${formatQty(f.quantity)}</td>
                <td class="mono">${formatPrice(f.price)}</td>
                <td class="mono">${formatCurrency(f.total)}</td>
                <td class="mono">${formatFee(f.fee, f.fee_currency)}</td>
                <td></td>
                <td></td>
            </tr>
        `).join('');

        parentRow.insertAdjacentHTML('afterend', fillRows);
        const count = toggleEl.textContent.replace('▶ ', '').replace('▼ ', '');
        toggleEl.textContent = `▼ ${count}`;
    } catch (err) {
        console.error('Failed to load fills:', err);
    }
}

function renderPagination(data) {
    setText('pagination-info', `${data.total} orders`);
    setText('pagination-page', `Page ${data.page} of ${data.total_pages}`);

    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');
    if (btnPrev) btnPrev.disabled = data.page <= 1;
    if (btnNext) btnNext.disabled = data.page >= data.total_pages;
}

// ---- Exchanges ----
async function loadExchanges() {
    try {
        const exchanges = await API.exchanges();
        const container = document.getElementById('exchanges-list');
        if (!container) return;

        if (exchanges.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">⟐</div>
                    <h3>No exchanges connected</h3>
                    <p>Connect your first exchange to start syncing trades.</p>
                    <button class="btn btn-primary" onclick="openModal('modal-add-exchange')">+ Add Exchange</button>
                </div>`;
            return;
        }

        container.innerHTML = exchanges.map(e => `
            <div class="exchange-card">
                <div class="exchange-info">
                    <h3>${e.label || capitalise(e.exchange)}</h3>
                    <div class="exchange-meta">
                        ${e.trade_count} trades · 
                        Last sync: ${e.last_sync_at ? formatTime(e.last_sync_at) : 'Never'} ·
                        Status: ${e.last_sync_status || 'Not synced'}
                    </div>
                </div>
                <div class="exchange-actions">
                    <button class="btn btn-secondary btn-sm" onclick="syncExchange('${e.id}')">Sync Now</button>
                    <button class="btn btn-danger btn-sm" onclick="removeExchange('${e.id}', '${e.label || e.exchange}')">Remove</button>
                </div>
            </div>
        `).join('');

        // Update sync log
        loadSyncLog();
    } catch (err) {
        console.error('Failed to load exchanges:', err);
    }
}

async function loadSyncLog() {
    try {
        const logs = await API.syncLog();
        const tbody = document.getElementById('sync-log-tbody');
        if (!tbody) return;

        if (logs.length === 0) {
            tbody.innerHTML = `<tr class="empty-row"><td colspan="5">
                <div class="table-empty">No sync history yet.</div>
            </td></tr>`;
            return;
        }

        tbody.innerHTML = logs.map(l => `
            <tr>
                <td class="mono">${formatTime(l.started_at)}</td>
                <td>${l.exchange_id}</td>
                <td>${statusBadge(l.status)}</td>
                <td class="mono">${l.trades_fetched}</td>
                <td style="color:var(--red); max-width:200px; overflow:hidden; text-overflow:ellipsis">${l.error_message || '—'}</td>
            </tr>
        `).join('');
    } catch (err) {
        // Non-critical
    }
}

async function syncExchange(id) {
    try {
        await API.syncExchange(id);
        loadExchanges();
    } catch (err) {
        alert('Sync failed: ' + err.message);
    }
}

async function removeExchange(id, name) {
    if (!confirm(`Remove connection to ${name}? Your synced trade data will be kept.`)) return;
    try {
        await API.removeExchange(id);
        loadExchanges();
    } catch (err) {
        alert('Remove failed: ' + err.message);
    }
}

// ---- Settings ----
async function loadSettings() {
    try {
        const health = await API.health();
        setText('info-version', health.version || '—');
        setText('info-api-status', health.status === 'ok' ? '● Connected' : '○ Error');
    } catch (err) {
        setText('info-api-status', '○ Unreachable');
    }
}

// ---- Modals ----
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.style.display = 'flex';
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.style.display = 'none';
}

// ---- Formatting Helpers ----
function formatCurrency(val) {
    if (val === null || val === undefined) return '$0.00';
    const sign = val >= 0 ? '' : '-';
    return `${sign}$${Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatQty(val) {
    if (val >= 1) return val.toLocaleString('en-US', { maximumFractionDigits: 4 });
    return val.toLocaleString('en-US', { maximumFractionDigits: 8 });
}

function formatPrice(val) {
    if (val >= 1) return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return val.toLocaleString('en-US', { maximumFractionDigits: 8 });
}

function formatFee(val, currency) {
    if (val === 0) return '—';
    return `${val.toFixed(4)} ${currency}`;
}

function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })
        + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function capitalise(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
}

function colourPnl(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('positive', 'negative');
    if (val > 0) el.classList.add('positive');
    if (val < 0) el.classList.add('negative');
}

function statusBadge(status) {
    const colours = { success: 'var(--green)', error: 'var(--red)', running: 'var(--yellow)' };
    const colour = colours[status] || 'var(--text-muted)';
    return `<span style="color:${colour}; font-weight:600">${status}</span>`;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ---- Global sync status ----
async function updateGlobalSyncStatus() {
    try {
        const exchanges = await API.exchanges();
        const dot = document.querySelector('.sync-dot');
        const text = document.querySelector('.sync-text');
        if (!dot || !text) return;

        if (exchanges.length === 0) {
            dot.className = 'sync-dot';
            text.textContent = 'No exchanges';
        } else {
            const hasError = exchanges.some(e => e.last_sync_status === 'error');
            dot.className = 'sync-dot ' + (hasError ? 'error' : 'connected');
            text.textContent = `${exchanges.length} exchange${exchanges.length > 1 ? 's' : ''}`;
        }
    } catch (err) {
        // Silent
    }
}

// ---- Event Listeners ----
document.addEventListener('DOMContentLoaded', () => {
    // Nav links
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            navigateTo(link.dataset.page);
        });
    });

    // Pagination
    document.getElementById('btn-prev')?.addEventListener('click', () => {
        if (tradesState.page > 1) { tradesState.page--; loadTrades(); }
    });
    document.getElementById('btn-next')?.addEventListener('click', () => {
        tradesState.page++;
        loadTrades();
    });

    // Trade filters
    ['filter-exchange', 'filter-pair', 'filter-side', 'filter-strategy',
     'filter-date-from', 'filter-date-to'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            tradesState.page = 1;
            loadTrades();
        });
    });

    // Clear filters
    document.getElementById('btn-clear-filters')?.addEventListener('click', () => {
        ['filter-exchange', 'filter-pair', 'filter-side', 'filter-strategy'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        ['filter-date-from', 'filter-date-to'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        tradesState.page = 1;
        loadTrades();
    });

    // Export
    document.getElementById('btn-export')?.addEventListener('click', () => {
        const format = document.getElementById('export-format')?.value || 'xlsx';
        const filters = getTradeFilters();
        const params = new URLSearchParams();
        params.set('format', format);
        params.set('order_by', tradesState.orderBy);
        params.set('order_dir', tradesState.orderDir);
        Object.entries(filters).forEach(([k, v]) => {
            if (v) params.set(k, v);
        });
        // Trigger download via direct navigation
        window.location.href = `/api/export?${params.toString()}`;
    });

    // Sortable columns
    document.querySelectorAll('.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sort;
            if (tradesState.orderBy === col) {
                tradesState.orderDir = tradesState.orderDir === 'asc' ? 'desc' : 'asc';
            } else {
                tradesState.orderBy = col;
                tradesState.orderDir = 'desc';
            }
            tradesState.page = 1;
            loadTrades();
        });
    });

    // Add exchange buttons
    document.getElementById('btn-add-exchange')?.addEventListener('click', () => openModal('modal-add-exchange'));
    document.getElementById('btn-add-exchange-2')?.addEventListener('click', () => openModal('modal-add-exchange'));

    // Submit exchange
    document.getElementById('btn-submit-exchange')?.addEventListener('click', async () => {
        const body = {
            exchange: document.getElementById('new-exchange-type')?.value,
            api_key: document.getElementById('new-exchange-key')?.value,
            api_secret: document.getElementById('new-exchange-secret')?.value,
            passphrase: document.getElementById('new-exchange-passphrase')?.value || null,
            label: document.getElementById('new-exchange-label')?.value || null,
        };
        if (!body.exchange || !body.api_key || !body.api_secret) {
            alert('Please fill in exchange, API key, and secret.');
            return;
        }
        try {
            await API.addExchange(body);
            closeModal('modal-add-exchange');
            loadExchanges();
        } catch (err) {
            alert('Failed to add exchange: ' + err.message);
        }
    });

    // Close modals on backdrop click
    document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', e => {
            if (e.target === backdrop) backdrop.style.display = 'none';
        });
    });

    // Initial load
    navigateTo('overview');
    updateGlobalSyncStatus();
});
