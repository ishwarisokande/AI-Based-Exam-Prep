const API_BASE_URL = (() => {
    if (window.location.protocol === 'file:') {
        return 'http://127.0.0.1:8000/api/v1';
    }
    return '/api/v1';
})();

const api = {
    // Helper for fetch with Auth header
    async request(endpoint, options = {}) {
        const token = localStorage.getItem('token');
        const headers = {
            ...options.headers,
        };

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers
        });

        if (response.status === 401 || response.status === 403) {
            // Handle unauthorized - maybe redirect to login
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            if (!window.location.pathname.endsWith('login.html') && !window.location.pathname.endsWith('register.html') && !window.location.pathname.endsWith('index.html')) {
                const isSubdir = window.location.pathname.includes('/user/') || window.location.pathname.includes('/admin/');
                window.location.href = isSubdir ? '../login.html' : 'login.html';
            }
        }

        if (!response.ok) {
            let errorMessage = `Request failed with status ${response.status}`;
            try {
                const error = await response.json();
                if (error && error.message) {
                    errorMessage = error.message;
                }
            } catch (_jsonErr) {
                const text = await response.text().catch(() => 'An unknown error occurred');
                if (text) {
                    errorMessage = text;
                }
            }
            throw new Error(errorMessage);
        }

        try {
            return await response.json();
        } catch (err) {
            const text = await response.text().catch(() => null);
            throw new Error(text || 'Unable to parse server response');
        }
    },

    // Auth endpoints
    async login(email, password) {
        const data = await this.request('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify({ name: data.name, email: data.email, role: data.role }));
        return data;
    },

    async register(name, email, password) {
        const data = await this.request('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password })
        });
        localStorage.setItem('token', data.token);
        localStorage.setItem('user', JSON.stringify({ name: data.name, email: data.email, role: data.role }));
        return data;
    },

    // Dashboard & Materials
    async getDashboardStats() {
        return this.request('/dashboard/stats');
    },

    async getMaterials() {
        return this.request('/materials');
    },

    async getPerformanceAnalytics() {
        return this.request('/dashboard/analytics');
    },

    async uploadMaterial(formData) {
        // Fetch will automatically set content-type for FormData with boundary
        return this.request('/materials/upload', {
            method: 'POST',
            body: formData
        });
    },

    async getMaterialContent(id) {
        return this.request(`/materials/${id}/content`);
    },

    // Quiz Result
    async getQuizHistory() {
        return this.request('/quiz/history');
    },

    async saveQuizResult(score, total, materialId) {
        return this.request('/quiz/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score, total, materialId })
        });
    },

    // Admin Endpoints
    async getAdminStats() {
        return this.request('/admin/stats');
    },

    async getAllUsers() {
        return this.request('/admin/users');
    },

    async deleteUser(id) {
        return this.request(`/admin/users/${id}`, { method: 'DELETE' });
    },

    async getAllAdminContent() {
        return this.request('/admin/content');
    },

    async deleteAdminContent(id) {
        return this.request(`/admin/content/${id}`, { method: 'DELETE' });
    },

    async getAllMCQs() {
        return this.request('/admin/mcqs');
    },

    async deleteMCQ(id) {
        return this.request(`/admin/mcqs/${id}`, { method: 'DELETE' });
    },

    async getGlobalAnalytics() {
        return this.request('/admin/analytics/detailed');
    },

    async getSystemLogs() {
        return this.request('/admin/logs');
    },

    logout() {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.href = 'index.html';
    }
};

// Check auth on load for protected pages
if (!localStorage.getItem('token') &&
    !window.location.pathname.endsWith('login.html') &&
    !window.location.pathname.endsWith('register.html') &&
    !window.location.pathname.endsWith('index.html') &&
    window.location.pathname !== '/' &&
    window.location.pathname !== '/frontend/') {
    const isSubdir = window.location.pathname.includes('/user/') || window.location.pathname.includes('/admin/');
    window.location.href = isSubdir ? '../login.html' : 'login.html';
}
