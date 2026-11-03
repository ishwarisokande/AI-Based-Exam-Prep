/**
 * AI ExamPrep Component Loader
 * Dynamically loads and injects shared HTML components.
 */

const components = {
    load: async (id, path, currentPage) => {
        const placeholder = document.getElementById(id);
        if (!placeholder) return;

        try {
            const response = await fetch(path);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const html = await response.text();
            placeholder.innerHTML = html;

            // Post-load actions
            if (id === 'navbar-placeholder') {
                components.adjustNavbar(placeholder);
                components.highlightActiveLink(placeholder, currentPage);
            } else if (id === 'sidebar-placeholder') {
                components.highlightActiveLink(placeholder, currentPage);
            }

            if (id === 'topbar-placeholder') {
                components.updateUserInfo();
                components.updatePageTitle();
            }
        } catch (error) {
            console.error(`Failed to load component ${path}:`, error);
        }
    },

    adjustNavbar: (container) => {
        const token = localStorage.getItem('token');
        const user = JSON.parse(localStorage.getItem('user'));
        const navLinks = container.querySelector('#nav-links');
        const navCta = container.querySelector('.nav-cta');
        
        if (!navLinks) return;

        if (token && user) {
            // User is logged in
            navLinks.innerHTML = `
                <li><a href="index.html" data-page="index">Home</a></li>
                <li><a href="index.html#features" data-page="features">Features</a></li>
                <li><a href="index.html#how-it-works" data-page="how-it-works">How It Works</a></li>
                <li><a href="index.html#about" data-page="about">About Us</a></li>
                <li><a href="user/dashboard.html" data-page="dashboard">Dashboard</a></li>
                <li><a href="user/profile.html" data-page="profile">Profile</a></li>
                <li><a href="#" onclick="api.logout(); return false;">Logout</a></li>
            `;
            if (navCta) {
                navCta.innerHTML = `<a href="user/dashboard.html" class="btn btn-primary">Dashboard</a>`;
            }
        } else {
            // User is logged out
            navLinks.innerHTML = `
                <li><a href="index.html" data-page="index">Home</a></li>
                <li><a href="index.html#features" data-page="features">Features</a></li>
                <li><a href="index.html#how-it-works" data-page="how-it-works">How It Works</a></li>
                <li><a href="index.html#about" data-page="about">About Us</a></li>
                <li><a href="login.html" data-page="login">Login</a></li>
                <li><a href="register.html" data-page="register">Sign Up</a></li>
            `;
            if (navCta) {
                navCta.innerHTML = `<a href="register.html" class="btn btn-primary">Try Now</a>`;
            }
        }
    },

    highlightActiveLink: (container, currentPage) => {
        const links = container.querySelectorAll('[data-page]');
        links.forEach(link => {
            if (link.getAttribute('data-page') === currentPage) {
                link.classList.add(container.id === 'sidebar-placeholder' ? 'active' : 'active');
                // Note: navbar might need a different class if 'active' isn't standard there
            }
        });
    },

    updateUserInfo: () => {
        const user = JSON.parse(localStorage.getItem('user'));
        if (user) {
            document.querySelectorAll('.user-name').forEach(el => el.innerText = user.name);
            document.querySelectorAll('.user-role').forEach(el => el.innerText = user.role);
            document.querySelectorAll('.avatar').forEach(el => {
                const initials = user.name.split(' ').map(n => n[0]).join('').toUpperCase();
                el.innerText = initials;
            });
        }
    },

    updatePageTitle: () => {
        const titleEl = document.getElementById('dynamic-page-title');
        if (titleEl) {
            titleEl.innerText = document.title.split(' - ')[0];
        }
    }
};

// Global toggle sidebar function
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
        sidebar.classList.toggle('open');
    }
}

// Global toggle mobile menu for top navbar
function toggleMobileMenu() {
    const navLinks = document.getElementById('nav-links');
    if (navLinks) {
        navLinks.classList.toggle('active');
        // If hidden by default in CSS, we need to ensure .active overrides it
    }
}
