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
        case 'overview':    loadOverview(); break;
        case 'trades':      loadTrades();   break;
        case 'pnl':         loadPnl();      break;
        case 'strategies':  loadStrategies(); break;
        case 'notes':       loadNotes();    break;
        case 'exchanges':   loadExchanges(); break;
        case 'settings':    loadSettings();  break;
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

        // Check for stale sync
        checkStaleSyncWarning(data);

        // Load patterns and equity curve
        loadPatterns();
        loadEquityCurve();
    } catch (err) {
        console.error('Failed to load overview:', err);
    }
}

async function loadEquityCurve() {
    try {
        const data = await API.equityCurve();
        const card = document.getElementById('equity-curve-card');
        if (!data || data.length < 2) {
            if (card) card.style.display = 'none';
            return;
        }
        if (card) card.style.display = 'block';

        const container = document.getElementById('equity-chart');
        container.innerHTML = '';

        const chart = LightweightCharts.createChart(container, {
            layout: { background: { color: '#14151a' }, textColor: '#8b8d98' },
            grid: { vertLines: { color: '#2a2b33' }, horzLines: { color: '#2a2b33' } },
            width: container.clientWidth,
            height: 300,
            crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
            rightPriceScale: { borderColor: '#2a2b33' },
            timeScale: { borderColor: '#2a2b33' },
        });

        const lastPnl = data[data.length - 1].cumulative_pnl;
        const isPositive = lastPnl >= 0;
        const lineColor = isPositive ? '#34d399' : '#f87171';
        const topColor = isPositive ? 'rgba(52, 211, 153, 0.25)' : 'rgba(248, 113, 113, 0.25)';
        const bottomColor = isPositive ? 'rgba(52, 211, 153, 0.02)' : 'rgba(248, 113, 113, 0.02)';

        const series = chart.addAreaSeries({
            lineColor: lineColor,
            topColor: topColor,
            bottomColor: bottomColor,
            lineWidth: 2,
        });

        series.setData(data.map(d => ({ time: d.date, value: d.cumulative_pnl })));
        chart.timeScale().fitContent();

        new ResizeObserver(() => {
            chart.applyOptions({ width: container.clientWidth });
        }).observe(container);
    } catch (err) {
        // Non-critical — equity curve is optional
    }
}

function checkStaleSyncWarning(overviewData) {
    const warning = document.getElementById('stale-sync-warning');
    const text = document.getElementById('stale-sync-text');
    if (!warning || !text) return;

    if (overviewData.connected_exchanges === 0) {
        warning.style.display = 'none';
        return;
    }

    if (!overviewData.last_sync) {
        warning.style.display = 'flex';
        text.textContent = 'Your exchanges have never been synced. Go to Exchanges and click Sync Now.';
        return;
    }

    const lastSync = new Date(overviewData.last_sync);
    const hoursAgo = (Date.now() - lastSync.getTime()) / (1000 * 60 * 60);

    if (hoursAgo > 24) {
        const daysAgo = Math.floor(hoursAgo / 24);
        warning.style.display = 'flex';
        text.textContent = `Last sync was ${daysAgo} day${daysAgo > 1 ? 's' : ''} ago. Your trade data may be outdated.`;
    } else {
        warning.style.display = 'none';
    }
}

// ---- PnL ----
async function loadPnl() {
    const groupBy = document.getElementById('pnl-group-by')?.value || 'pair';
    try {
        const data = await API.pnl({ group_by: groupBy });

        setText('pnl-total', formatCurrency(data.total_pnl));
        setText('pnl-win-rate', data.trade_count > 0 ? `${data.win_rate}%` : '—');
        setText('pnl-profit-factor', data.profit_factor > 0 ? data.profit_factor : '—');
        setText('pnl-trade-count', data.trade_count);
        setText('pnl-avg-win', formatCurrency(data.avg_win));
        setText('pnl-avg-loss', formatCurrency(data.avg_loss));
        setText('pnl-best-pair', data.best_pair || '—');
        setText('pnl-worst-pair', data.worst_pair || '—');

        colourPnl('pnl-total', data.total_pnl);

        // Table title
        const titles = { pair: 'PnL by Pair', exchange: 'PnL by Exchange', strategy: 'PnL by Strategy', month: 'PnL by Month', week: 'PnL by Week' };
        setText('pnl-table-title', titles[groupBy] || 'PnL Breakdown');

        // Render breakdown table
        const tbody = document.getElementById('pnl-tbody');
        if (!tbody) return;

        if (!data.breakdown || data.breakdown.length === 0) {
            tbody.innerHTML = `<tr class="empty-row"><td colspan="5">
                <div class="table-empty">No PnL data. Complete a buy+sell round-trip to see results.</div>
            </td></tr>`;
            return;
        }

        tbody.innerHTML = data.breakdown.map(b => {
            const pnlClass = b.total_pnl >= 0 ? 'positive' : 'negative';
            return `
            <tr>
                <td><strong>${b.group}</strong></td>
                <td class="mono">${b.trade_count}</td>
                <td class="mono ${pnlClass}">${formatCurrency(b.total_pnl)}</td>
                <td class="mono">${formatCurrency(b.total_fees)}</td>
                <td class="mono">${b.win_rate}%</td>
            </tr>`;
        }).join('');
    } catch (err) {
        console.error('Failed to load PnL:', err);
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
        exchange:   document.getElementById('filter-exchange')?.value || '',
        pair:       document.getElementById('filter-pair')?.value || '',
        trade_type: document.getElementById('filter-trade-type')?.value || '',
        side:       document.getElementById('filter-side')?.value || '',
        strategy:   document.getElementById('filter-strategy')?.value || '',
        date_from:  document.getElementById('filter-date-from')?.value || '',
        date_to:    document.getElementById('filter-date-to')?.value || '',
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
        _strategyCache = strategies;  // Update cache
        const stratSelect = document.getElementById('filter-strategy');
        if (stratSelect && stratSelect.options.length <= 2) {
            strategies.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.textContent = s.name;
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
        tbody.innerHTML = `<tr class="empty-row"><td colspan="11">
            <div class="table-empty">No trades found matching your filters.</div>
        </td></tr>`;
        return;
    }

    tbody.innerHTML = trades.map(t => {
        const hasMultipleFills = t.fill_count > 1;
        const expandBtn = hasMultipleFills
            ? `<span class="fill-toggle" onclick="event.stopPropagation(); toggleFills('${t.order_id || t.id}', this)" title="Click to expand fills">▶ ${t.fill_count}</span>`
            : `<span style="color:var(--text-muted)">1</span>`;

        const strategyCell = t.strategy
            ? `<span class="strategy-badge" style="background:${getStrategyColour(t.strategy)}22; color:${getStrategyColour(t.strategy)}; border: 1px solid ${getStrategyColour(t.strategy)}44">${t.strategy}</span>`
            : '<span style="color:var(--text-muted)">—</span>';

        const typeBadge = t.trade_type === 'futures'
            ? ' <span class="type-badge futures">PERP</span>'
            : '';

        return `
        <tr data-id="${t.id}" data-order-id="${t.order_id || ''}" class="${hasMultipleFills ? 'expandable' : ''}" onclick="openTradeDetailFromRow(this)" data-trade='${JSON.stringify(t).replace(/'/g, "&#39;")}'>
            <td class="mono">${formatTime(t.timestamp)}</td>
            <td>${capitalise(t.exchange)}</td>
            <td><strong>${t.pair}</strong>${typeBadge}</td>
            <td class="side-${t.side}">${t.side.toUpperCase()}</td>
            <td class="mono">${formatQty(t.quantity)}</td>
            <td class="mono">${formatPrice(t.price)}</td>
            <td class="mono">${formatCurrency(t.total)}</td>
            <td class="mono">${formatFee(t.fee, t.fee_currency)}</td>
            <td class="strategy-cell" onclick="event.stopPropagation(); openTagPopup('${t.id}', this, '${t.strategy || ''}')">${strategyCell}</td>
            <td class="screenshots-cell" id="ss-${t.id}" onclick="event.stopPropagation()">
                <div class="ss-row" id="ssr-${t.id}">
                    <label class="ss-upload-btn" title="Add chart screenshot">
                        <input type="file" accept="image/*" style="display:none" onchange="uploadScreenshot('${t.id}', this.files[0])">
                        +
                    </label>
                </div>
            </td>
            <td class="fills-col" onclick="event.stopPropagation()">${expandBtn}</td>
        </tr>`;
    }).join('');

    // Load screenshots for visible trades
    loadTradeScreenshots(trades);
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

        container.innerHTML = exchanges.map(e => {
            let staleClass = '';
            let staleTag = '';
            if (e.last_sync_at) {
                const hoursAgo = (Date.now() - new Date(e.last_sync_at).getTime()) / (1000 * 60 * 60);
                if (hoursAgo > 24) {
                    staleClass = ' exchange-card-stale';
                    staleTag = `<span class="stale-tag">Stale — ${Math.floor(hoursAgo / 24)}d ago</span>`;
                }
            } else {
                staleTag = '<span class="stale-tag">Never synced</span>';
            }

            const statusColour = e.last_sync_status === 'success' ? 'var(--green)'
                : e.last_sync_status === 'error' ? 'var(--red)' : 'var(--text-muted)';

            return `
            <div class="exchange-card${staleClass}">
                <div class="exchange-info">
                    <h3>${e.label || capitalise(e.exchange)} ${staleTag}</h3>
                    <div class="exchange-meta">
                        ${e.trade_count} trades · 
                        Last sync: ${e.last_sync_at ? formatTime(e.last_sync_at) : 'Never'} ·
                        Status: <span style="color:${statusColour}; font-weight:600">${e.last_sync_status || 'Not synced'}</span>
                    </div>
                </div>
                <div class="exchange-actions">
                    <button class="btn btn-secondary btn-sm" onclick="syncExchange('${e.id}')">Sync Now</button>
                    <button class="btn btn-danger btn-sm" onclick="removeExchange('${e.id}', '${e.label || e.exchange}')">Remove</button>
                </div>
            </div>`;
        }).join('');

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
        showToast('Syncing trades...', 'info', 2000);
        const result = await API.syncExchange(id);
        showToast(`Sync complete — ${result.trades_fetched} new trades`, 'success');
        loadExchanges();
    } catch (err) {
        showToast('Sync failed: ' + err.message, 'error');
    }
}

async function removeExchange(id, name) {
    if (!confirm(`Remove connection to ${name}? Your synced trade data will be kept.`)) return;
    try {
        await API.removeExchange(id);
        loadExchanges();
    } catch (err) {
        showToast('Remove failed: ' + err.message, 'error');
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

// ---- Strategy colour cache ----
let _strategyCache = [];

function getStrategyColour(name) {
    const s = _strategyCache.find(s => s.name === name);
    return s ? s.colour : '#6c9cfc';
}

// ---- Strategies Page ----
async function loadStrategies() {
    try {
        const strategies = await API.strategies();
        _strategyCache = strategies;
        const container = document.getElementById('strategies-list');
        if (!container) return;

        if (strategies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📖</div>
                    <h3>No strategies yet</h3>
                    <p>Create your first strategy to start building your playbook.</p>
                    <button class="btn btn-primary" onclick="openStrategyModal()">+ New Strategy</button>
                </div>`;
            return;
        }

        container.innerHTML = strategies.map(s => `
            <div class="strategy-card" style="border-left: 4px solid ${s.colour}">
                <div class="strategy-card-header">
                    <div>
                        <h3 class="strategy-card-name" style="color: ${s.colour}">${s.name}</h3>
                        ${s.description ? `<p class="strategy-card-desc">${s.description}</p>` : ''}
                    </div>
                    <div class="strategy-card-actions">
                        <span class="strategy-trade-count">${s.trade_count} trade${s.trade_count !== 1 ? 's' : ''}</span>
                        <button class="btn btn-ghost btn-sm" onclick="openStrategyModal('${s.id}')">Edit</button>
                        <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="confirmDeleteStrategy('${s.id}', '${s.name}')">Delete</button>
                    </div>
                </div>
                ${s.playbook ? `<div class="strategy-playbook-display"><pre>${escapeHtml(s.playbook)}</pre></div>` : ''}
            </div>
        `).join('');
    } catch (err) {
        console.error('Failed to load strategies:', err);
    }
}

function openStrategyModal(editId = null) {
    const modal = document.getElementById('modal-strategy');
    const title = document.getElementById('strategy-modal-title');
    const idField = document.getElementById('strategy-edit-id');

    // Reset form
    document.getElementById('strategy-name').value = '';
    document.getElementById('strategy-description').value = '';
    document.getElementById('strategy-playbook').value = '';
    document.querySelectorAll('.colour-swatch').forEach(s => s.classList.remove('active'));
    document.querySelector('.colour-swatch[data-colour="#6c9cfc"]')?.classList.add('active');

    if (editId) {
        title.textContent = 'Edit Strategy';
        idField.value = editId;
        // Load existing data
        const s = _strategyCache.find(s => s.id === editId);
        if (s) {
            document.getElementById('strategy-name').value = s.name;
            document.getElementById('strategy-description').value = s.description || '';
            document.getElementById('strategy-playbook').value = s.playbook || '';
            document.querySelectorAll('.colour-swatch').forEach(sw => {
                sw.classList.toggle('active', sw.dataset.colour === s.colour);
            });
        }
    } else {
        title.textContent = 'New Strategy';
        idField.value = '';
    }

    modal.style.display = 'flex';
}

async function saveStrategy() {
    const editId = document.getElementById('strategy-edit-id').value;
    const name = document.getElementById('strategy-name').value.trim();
    const description = document.getElementById('strategy-description').value.trim();
    const playbook = document.getElementById('strategy-playbook').value;
    const activeSwatch = document.querySelector('.colour-swatch.active');
    const colour = activeSwatch ? activeSwatch.dataset.colour : '#6c9cfc';

    if (!name) {
        showToast('Please enter a strategy name.', 'warning');
        return;
    }

    try {
        if (editId) {
            await API.updateStrategy(editId, { name, description, colour, playbook });
        } else {
            await API.createStrategy({ name, description, colour, playbook });
        }
        closeModal('modal-strategy');
        loadStrategies();
    } catch (err) {
        showToast('Failed to save strategy: ' + err.message, 'error');
    }
}

function confirmDeleteStrategy(id, name) {
    document.getElementById('delete-strategy-name').textContent = name;
    document.getElementById('btn-confirm-delete-strategy').onclick = async () => {
        try {
            await API.deleteStrategy(id);
            closeModal('modal-delete-strategy');
            loadStrategies();
        } catch (err) {
            showToast('Failed to delete: ' + err.message, 'error');
        }
    };
    openModal('modal-delete-strategy');
}

async function exportStrategies() {
    try {
        const data = await API.get('/strategies-export');
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'strategies_backup.json';
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        showToast('Export failed: ' + err.message, 'error');
    }
}

async function importStrategies() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            const result = await API.post('/strategies-import', data);
            showToast(`Imported ${result.imported} strategies, skipped ${result.skipped} duplicates.`, 'success');
            loadStrategies();
        } catch (err) {
            showToast('Import failed: ' + err.message, 'error');
        }
    };
    input.click();
}

// ---- Trade Tagging Popup ----
function openTagPopup(tradeId, cell, currentStrategy) {
    // Close any existing popup
    closeTagPopup();

    const strategies = _strategyCache;
    const options = strategies.map(s =>
        `<div class="tag-option ${s.name === currentStrategy ? 'active' : ''}" onclick="tagTrade('${tradeId}', '${s.name}')">
            <span class="tag-dot" style="background:${s.colour}"></span>
            ${s.name}
        </div>`
    ).join('');

    const clearOption = currentStrategy
        ? `<div class="tag-option tag-clear" onclick="tagTrade('${tradeId}', '')">✕ Remove tag</div>`
        : '';

    const noStrategies = strategies.length === 0
        ? `<div class="tag-empty">No strategies yet.<br><a href="#" onclick="event.preventDefault(); closeTagPopup(); navigateTo('strategies')">Create one</a></div>`
        : '';

    const popup = document.createElement('div');
    popup.className = 'tag-popup';
    popup.id = 'tag-popup';
    popup.innerHTML = `
        <div class="tag-popup-header">Assign Strategy</div>
        ${noStrategies}
        ${options}
        ${clearOption}
    `;

    cell.style.position = 'relative';
    cell.appendChild(popup);

    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', _closeTagHandler, { once: true });
    }, 10);
}

function _closeTagHandler(e) {
    const popup = document.getElementById('tag-popup');
    if (popup && !popup.contains(e.target)) {
        closeTagPopup();
    }
}

function closeTagPopup() {
    const popup = document.getElementById('tag-popup');
    if (popup) popup.remove();
    document.removeEventListener('click', _closeTagHandler);
}

async function tagTrade(tradeId, strategyName) {
    try {
        await API.updateTrade(tradeId, { strategy: strategyName || '' });
        closeTagPopup();
        loadTrades();  // Refresh table
    } catch (err) {
        showToast('Failed to tag trade: ' + err.message, 'error');
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ---- Trade Screenshots ----
async function loadTradeScreenshots(trades) {
    for (const t of trades) {
        try {
            const screenshots = await API.tradeScreenshots(t.id);
            const container = document.getElementById(`ssr-${t.id}`);
            if (!container) continue;

            // Build thumbnails
            const thumbs = screenshots.map(s =>
                `<div class="ss-thumb-wrap">
                    <img class="ss-thumb" src="${s.url}" alt="${s.original_name}" onclick="openLightbox('${s.url}')" title="Click to enlarge">
                    <button class="ss-delete" onclick="event.stopPropagation(); deleteScreenshot('${s.id}', '${t.id}')" title="Remove">✕</button>
                </div>`
            ).join('');

            // Show upload button only if under limit (2)
            const uploadBtn = screenshots.length < 2
                ? `<label class="ss-upload-btn" title="Add chart screenshot">
                       <input type="file" accept="image/*" style="display:none" onchange="uploadScreenshot('${t.id}', this.files[0])">
                       +
                   </label>`
                : '';

            container.innerHTML = thumbs + uploadBtn;
        } catch (err) {
            // Non-critical — thumbnails just won't load
        }
    }
}

async function uploadScreenshot(tradeId, file) {
    if (!file) return;
    try {
        await API.uploadScreenshot(tradeId, file);
        loadTrades();  // Refresh to show new thumbnail
    } catch (err) {
        showToast('Upload failed: ' + err.message, 'error');
    }
}

async function deleteScreenshot(screenshotId, tradeId) {
    try {
        await API.deleteScreenshot(screenshotId);
        loadTrades();  // Refresh
    } catch (err) {
        showToast('Delete failed: ' + err.message, 'error');
    }
}

function openLightbox(url) {
    const lb = document.getElementById('lightbox');
    const img = document.getElementById('lightbox-img');
    if (!lb || !img) return;
    img.src = url;
    lb.style.display = 'flex';
}

function closeLightbox() {
    const lb = document.getElementById('lightbox');
    if (lb) lb.style.display = 'none';
}

// ---- Patterns ----
async function loadPatterns() {
    try {
        const data = await API.patterns();
        const section = document.getElementById('patterns-section');
        const container = document.getElementById('patterns-container');
        if (!section || !container) return;

        if (!data.patterns || data.patterns.length === 0) {
            section.style.display = 'none';
            return;
        }

        section.style.display = 'block';
        container.innerHTML = data.patterns.map(p => {
            const icon = p.type === 'streaks' ? '🔥' : p.type === 'time_of_day' ? '🕐' : '⏱';
            let detailHtml = '';

            if (p.type === 'streaks' && p.data) {
                detailHtml = `
                    <div class="pattern-stats">
                        <span>Best win streak: <strong>${p.data.max_win_streak}</strong></span>
                        <span>Worst loss streak: <strong>${p.data.max_loss_streak}</strong></span>
                        <span>Current: <strong>${p.data.current_streak} ${p.data.current_streak_type}${p.data.current_streak > 1 ? 's' : ''}</strong></span>
                    </div>`;
            } else if (p.type === 'time_of_day' && p.data) {
                const best = p.data.best_hour;
                const worst = p.data.worst_hour;
                detailHtml = `
                    <div class="pattern-stats">
                        ${best ? `<span>Best hour: <strong>${best.hour}:00 UTC</strong> (${formatCurrency(best.pnl)})</span>` : ''}
                        ${worst ? `<span>Worst hour: <strong>${worst.hour}:00 UTC</strong> (${formatCurrency(worst.pnl)})</span>` : ''}
                    </div>`;
            } else if (p.type === 'hold_time' && p.data) {
                detailHtml = `
                    <div class="pattern-stats">
                        <span>Average: <strong>${p.data.avg_formatted}</strong></span>
                        <span>Shortest: <strong>${p.data.shortest.pair}</strong> (${p.data.shortest.duration})</span>
                        <span>Longest: <strong>${p.data.longest.pair}</strong> (${p.data.longest.duration})</span>
                    </div>`;
            }

            return `
            <div class="pattern-card">
                <div class="pattern-header">
                    <span class="pattern-icon">${icon}</span>
                    <strong>${p.title}</strong>
                </div>
                <p class="pattern-insight">${p.insight}</p>
                ${detailHtml}
            </div>`;
        }).join('');
    } catch (err) {
        // Non-critical
    }
}

// ---- Notes Page ----
async function loadNotes() {
    try {
        const notes = await API.notes();
        const container = document.getElementById('notes-list');
        if (!container) return;

        if (notes.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📝</div>
                    <h3>No notes yet</h3>
                    <p>Create a note to jot down ideas, research, or observations (max 4).</p>
                </div>`;
            return;
        }

        container.innerHTML = notes.map(n => `
            <div class="note-card" id="note-${n.id}">
                <div class="note-card-header">
                    <input class="note-title-input" value="${escapeHtml(n.title)}" onchange="updateNote('${n.id}', {title: this.value})">
                    <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteNote('${n.id}')">Delete</button>
                </div>
                <textarea class="note-content" rows="10" placeholder="Write your notes here..." onblur="updateNote('${n.id}', {content: this.value})">${escapeHtml(n.content)}</textarea>
                <div class="note-meta">Last updated: ${formatTime(n.updated_at)}</div>
            </div>
        `).join('');
    } catch (err) {
        console.error('Failed to load notes:', err);
    }
}

async function createNote() {
    try {
        await API.createNote({ title: 'New Note', content: '' });
        loadNotes();
    } catch (err) {
        showToast('Failed to create note: ' + err.message, 'error');
    }
}

async function updateNote(id, updates) {
    try {
        await API.updateNote(id, updates);
        // Don't reload — just update the meta silently
    } catch (err) {
        showToast('Failed to save note: ' + err.message, 'error');
    }
}

async function deleteNote(id) {
    if (!confirm('Delete this note?')) return;
    try {
        await API.deleteNote(id);
        loadNotes();
    } catch (err) {
        showToast('Failed to delete note: ' + err.message, 'error');
    }
}

// ---- Manual Trade Entry ----
function openAddTradeModal() {
    // Reset form
    ['mt-exchange', 'mt-pair', 'mt-quantity', 'mt-price', 'mt-order-id', 'mt-notes'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    document.getElementById('mt-exchange').value = 'manual';
    document.getElementById('mt-fee').value = '0';
    document.getElementById('mt-fee-currency').value = 'USDT';
    document.getElementById('mt-trade-type').value = 'spot';
    document.getElementById('mt-side').value = 'buy';
    document.getElementById('mt-timestamp').value = '';

    // Populate strategy dropdown
    const stratSelect = document.getElementById('mt-strategy');
    if (stratSelect) {
        while (stratSelect.options.length > 1) stratSelect.remove(1);
        _strategyCache.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.name;
            opt.textContent = s.name;
            stratSelect.appendChild(opt);
        });
    }

    openModal('modal-add-trade');
}

async function submitManualTrade() {
    const pair = document.getElementById('mt-pair')?.value?.trim().toUpperCase();
    const qty = parseFloat(document.getElementById('mt-quantity')?.value);
    const price = parseFloat(document.getElementById('mt-price')?.value);

    if (!pair || !pair.includes('/')) {
        showToast('Enter a valid pair (e.g. BTC/USDT)', 'warning');
        return;
    }
    if (isNaN(qty) || qty <= 0) {
        showToast('Enter a valid quantity', 'warning');
        return;
    }
    if (isNaN(price) || price <= 0) {
        showToast('Enter a valid price', 'warning');
        return;
    }

    const tsInput = document.getElementById('mt-timestamp')?.value;

    try {
        const body = {
            exchange: document.getElementById('mt-exchange')?.value || 'manual',
            pair: pair,
            side: document.getElementById('mt-side')?.value || 'buy',
            quantity: qty,
            price: price,
            fee: parseFloat(document.getElementById('mt-fee')?.value) || 0,
            fee_currency: document.getElementById('mt-fee-currency')?.value || 'USDT',
            trade_type: document.getElementById('mt-trade-type')?.value || 'spot',
            timestamp: tsInput ? new Date(tsInput).toISOString() : null,
            strategy: document.getElementById('mt-strategy')?.value || null,
            notes: document.getElementById('mt-notes')?.value || null,
            order_id: document.getElementById('mt-order-id')?.value || null,
        };

        await API.createTrade(body);
        closeModal('modal-add-trade');
        showToast(`Trade added: ${body.side.toUpperCase()} ${body.pair}`, 'success');
        loadTrades();
    } catch (err) {
        showToast('Failed to add trade: ' + err.message, 'error');
    }
}

// ---- Trade Detail Panel ----
let _currentDetailTradeId = null;
let _emotionTagCache = [];

async function refreshEmotionTags() {
    try { _emotionTagCache = await API.emotionTags(); } catch { _emotionTagCache = []; }
}

function setRatingStars(value) {
    document.querySelectorAll('#trade-detail-rating .rating-star').forEach(star => {
        star.classList.toggle('active', parseInt(star.dataset.value) <= value);
    });
}

function openTradeDetail(tradeId, tradeData) {
    _currentDetailTradeId = tradeId;

    const title = document.getElementById('trade-detail-title');
    const info = document.getElementById('trade-detail-info');
    const notes = document.getElementById('trade-detail-notes');

    if (title) title.textContent = `${tradeData.pair} — ${tradeData.side.toUpperCase()}`;

    if (info) {
        const typeBadge = tradeData.trade_type === 'futures' ? '<span class="type-badge futures">PERP</span>' : '';
        info.innerHTML = `
            <div class="detail-grid">
                <div class="detail-item"><span class="detail-label">Time</span><span class="detail-value mono">${formatTime(tradeData.timestamp)}</span></div>
                <div class="detail-item"><span class="detail-label">Exchange</span><span class="detail-value">${capitalise(tradeData.exchange)}</span></div>
                <div class="detail-item"><span class="detail-label">Pair</span><span class="detail-value"><strong>${tradeData.pair}</strong> ${typeBadge}</span></div>
                <div class="detail-item"><span class="detail-label">Side</span><span class="detail-value side-${tradeData.side}">${tradeData.side.toUpperCase()}</span></div>
                <div class="detail-item"><span class="detail-label">Quantity</span><span class="detail-value mono">${formatQty(tradeData.quantity)}</span></div>
                <div class="detail-item"><span class="detail-label">Price</span><span class="detail-value mono">${formatPrice(tradeData.price)}</span></div>
                <div class="detail-item"><span class="detail-label">Total</span><span class="detail-value mono">${formatCurrency(tradeData.total)}</span></div>
                <div class="detail-item"><span class="detail-label">Fee</span><span class="detail-value mono">${formatFee(tradeData.fee, tradeData.fee_currency)}</span></div>
            </div>`;
    }

    if (notes) notes.value = tradeData.notes || '';

    // Populate emotion tag dropdown
    const emotionSelect = document.getElementById('trade-detail-emotion');
    if (emotionSelect) {
        emotionSelect.innerHTML = '<option value="">No emotion tag</option>';
        _emotionTagCache.forEach(tag => {
            const opt = document.createElement('option');
            opt.value = tag.name;
            opt.textContent = tag.name;
            opt.style.color = tag.colour;
            if (tradeData.emotion_tag === tag.name) opt.selected = true;
            emotionSelect.appendChild(opt);
        });
    }

    // Set rating stars
    setRatingStars(tradeData.trade_rating || 0);

    openModal('modal-trade-detail');
}

function openTradeDetailFromRow(row) {
    try {
        const data = JSON.parse(row.dataset.trade);
        openTradeDetail(data.id, data);
    } catch (err) {
        console.error('Failed to open trade detail:', err);
    }
}

async function saveTradeDetail() {
    if (!_currentDetailTradeId) return;
    const notes = document.getElementById('trade-detail-notes')?.value || '';
    const emotionTag = document.getElementById('trade-detail-emotion')?.value || '';
    const activeStar = document.querySelector('#trade-detail-rating .rating-star.active:last-of-type');
    const rating = activeStar ? parseInt(activeStar.dataset.value) : 0;

    try {
        await API.updateTrade(_currentDetailTradeId, {
            notes,
            emotion_tag: emotionTag,
            trade_rating: rating,
        });
        closeModal('modal-trade-detail');
        showToast('Trade updated', 'success');
        loadTrades();
    } catch (err) {
        showToast('Failed to save: ' + err.message, 'error');
    }
}

// ---- Toast Notifications ----
function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) { alert(message); return; }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span class="toast-msg">${message}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto-remove
    if (duration > 0) {
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
}

// ---- Backup & Restore ----
async function downloadBackup() {
    try {
        showToast('Preparing backup...', 'info', 2000);
        window.location.href = '/api/backup';
    } catch (err) {
        showToast('Backup failed: ' + err.message, 'error');
    }
}

async function restoreBackup(file) {
    if (!file) return;
    if (!confirm('This will replace your current database and screenshots with the backup. Are you sure?')) return;

    try {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/restore', { method: 'POST', body: form });
        const data = await res.json();

        if (!res.ok) {
            showToast('Restore failed: ' + (data.detail || 'Unknown error'), 'error');
            return;
        }

        showToast(`Backup restored! ${data.screenshots_restored} screenshots recovered. Reloading...`, 'success', 3000);
        setTimeout(() => window.location.reload(), 3000);
    } catch (err) {
        showToast('Restore failed: ' + err.message, 'error');
    }
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
    ['filter-exchange', 'filter-pair', 'filter-trade-type', 'filter-side', 'filter-strategy',
     'filter-date-from', 'filter-date-to'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            tradesState.page = 1;
            loadTrades();
        });
    });

    // Clear filters
    document.getElementById('btn-clear-filters')?.addEventListener('click', () => {
        ['filter-exchange', 'filter-pair', 'filter-trade-type', 'filter-side', 'filter-strategy'].forEach(id => {
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
        const includeScreenshots = document.getElementById('export-screenshots')?.checked || false;
        const filters = getTradeFilters();
        const params = new URLSearchParams();
        params.set('format', format);
        params.set('order_by', tradesState.orderBy);
        params.set('order_dir', tradesState.orderDir);
        if (includeScreenshots) params.set('include_screenshots', 'true');
        Object.entries(filters).forEach(([k, v]) => {
            if (v) params.set(k, v);
        });
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

    // Show/hide passphrase field based on exchange
    document.getElementById('new-exchange-type')?.addEventListener('change', (e) => {
        const needs = ['gateio', 'kraken'].includes(e.target.value);
        document.getElementById('passphrase-group').style.display = needs ? 'block' : 'none';
    });

    // Submit exchange
    document.getElementById('btn-submit-exchange')?.addEventListener('click', async () => {
        const body = {
            exchange: document.getElementById('new-exchange-type')?.value,
            api_key: document.getElementById('new-exchange-key')?.value,
            api_secret: document.getElementById('new-exchange-secret')?.value,
            passphrase: document.getElementById('new-exchange-passphrase')?.value || null,
            label: document.getElementById('new-exchange-label')?.value || null,
            sync_start_date: document.getElementById('new-exchange-start-date')?.value || null,
        };
        if (!body.exchange || !body.api_key || !body.api_secret) {
            showToast('Please fill in exchange, API key, and secret.', 'warning');
            return;
        }
        try {
            await API.addExchange(body);
            closeModal('modal-add-exchange');
            loadExchanges();
        } catch (err) {
            showToast('Failed to add exchange: ' + err.message, 'error');
        }
    });

    // Close modals on backdrop click
    document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', e => {
            if (e.target === backdrop) backdrop.style.display = 'none';
        });
    });

    // PnL: group-by selector
    document.getElementById('pnl-group-by')?.addEventListener('change', () => loadPnl());

    // Manual trade
    document.getElementById('btn-add-trade')?.addEventListener('click', openAddTradeModal);
    document.getElementById('btn-submit-trade')?.addEventListener('click', submitManualTrade);

    // Trade detail notes
    document.getElementById('btn-save-trade-notes')?.addEventListener('click', saveTradeDetail);

    // Rating star click handlers
    document.querySelectorAll('#trade-detail-rating .rating-star').forEach(star => {
        star.addEventListener('click', () => {
            const val = parseInt(star.dataset.value);
            const current = document.querySelector('#trade-detail-rating .rating-star.active:last-of-type');
            const currentVal = current ? parseInt(current.dataset.value) : 0;
            setRatingStars(val === currentVal ? 0 : val);
        });
    });

    // Load emotion tags on startup
    refreshEmotionTags();

    // Notes page
    document.getElementById('btn-add-note')?.addEventListener('click', createNote);

    // Strategy: add button
    document.getElementById('btn-add-strategy')?.addEventListener('click', () => openStrategyModal());

    // Strategy: save button
    document.getElementById('btn-save-strategy')?.addEventListener('click', saveStrategy);

    // Strategy: colour picker
    document.querySelectorAll('.colour-swatch').forEach(swatch => {
        swatch.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.colour-swatch').forEach(s => s.classList.remove('active'));
            swatch.classList.add('active');
        });
    });

    // Strategy: update filter dropdown on trades page
    async function refreshStrategyFilter() {
        try {
            const strategies = await API.strategies();
            _strategyCache = strategies;
            const stratSelect = document.getElementById('filter-strategy');
            if (stratSelect) {
                // Keep first two options (All / Untagged)
                while (stratSelect.options.length > 2) stratSelect.remove(2);
                strategies.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s.name;
                    opt.textContent = s.name;
                    stratSelect.appendChild(opt);
                });
            }
        } catch (err) {
            // Non-critical
        }
    }

    // Preload strategy cache for trade colours
    refreshStrategyFilter();

    // Initial load
    navigateTo('overview');
    updateGlobalSyncStatus();
});
