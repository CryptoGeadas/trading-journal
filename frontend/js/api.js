/**
 * API client — thin wrapper around fetch for the trading journal backend.
 */
const API = {
    base: '/api',

    async get(path, params = {}) {
        const url = new URL(this.base + path, window.location.origin);
        Object.entries(params).forEach(([k, v]) => {
            if (v !== null && v !== undefined && v !== '') {
                url.searchParams.set(k, v);
            }
        });
        const res = await fetch(url);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API error');
        }
        return res.json();
    },

    async post(path, body = {}) {
        const res = await fetch(this.base + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API error');
        }
        return res.json();
    },

    async patch(path, body = {}) {
        const res = await fetch(this.base + path, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API error');
        }
        return res.json();
    },

    async delete(path, params = {}) {
        const url = new URL(this.base + path, window.location.origin);
        Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
        const res = await fetch(url, { method: 'DELETE' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API error');
        }
        return res.json();
    },

    // Convenience methods
    overview:     ()            => API.get('/overview'),
    trades:       (params = {}) => API.get('/trades', params),
    trade:        (id)          => API.get(`/trades/${id}`),
    fills:        (orderId)     => API.get(`/trades/fills/${orderId}`),
    updateTrade:  (id, body)    => API.patch(`/trades/${id}`, body),
    exchanges:    ()            => API.get('/exchanges'),
    addExchange:  (body)        => API.post('/exchanges', body),
    syncExchange: (id)          => API.post(`/exchanges/${id}/sync`),
    removeExchange: (id)        => API.delete(`/exchanges/${id}`, { confirm: true }),
    syncLog:      (params = {}) => API.get('/sync-log', params),
    pnl:          (params = {}) => API.get('/pnl', params),
    strategies:   ()            => API.get('/strategies'),
    pairs:        ()            => API.get('/pairs'),
    health:       ()            => fetch('/health').then(r => r.json()),
};
