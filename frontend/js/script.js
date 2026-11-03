// Basic JavaScript for animations and interactivity
document.addEventListener('DOMContentLoaded', () => {
    // Add smooth scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            document.querySelector(this.getAttribute('href')).scrollIntoView({
                behavior: 'smooth'
            });
        });
    });

    // Check if user is logged in (mock)
    const token = localStorage.getItem('token');
    if (token) {
        updateNavbarForLoggedInUser();
    }
});

function updateNavbarForLoggedInUser() {
    const navLinks = document.querySelector('.nav-links');
    const user = JSON.parse(localStorage.getItem('user'));
    if (!user) return;
    if (user.role === 'ADMIN') {
        navLinks.innerHTML = `
            <li><a href="../admin/dashboard.html">Admin Dashboard</a></li>
            <li><a href="../admin/users.html">Users</a></li>
            <li><a href="../admin/content.html">Content</a></li>
            <li><a href="../admin/analytics.html">Analytics</a></li>
            <li><button onclick="logout()" class="btn btn-outline">Logout</button></li>
        `;
    } else {
        navLinks.innerHTML = `
            <li><a href="index.html">Home</a></li>
            <li><a href="user/dashboard.html">Progress</a></li>
            <li><a href="user/upload.html">Upload</a></li>
            <li><a href="user/notes.html">Notes</a></li>
            <li><a href="user/practice.html">Practice</a></li>
            <li><a href="user/profile.html">Profile</a></li>
            <li><button onclick="logout()" class="btn btn-outline">Logout</button></li>
        `;
    }
}

function logout() {
    localStorage.removeItem('token');
    window.location.href = 'index.html';
}
