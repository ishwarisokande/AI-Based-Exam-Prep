function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
        sidebar.classList.toggle('open');
    }
}

// Performance Chart Initialization (Chart.js)
async function initDashboard() {
    try {
        // 1. Fetch Stats
        const stats = await api.getDashboardStats();

        // Update Stats UI
        const statElements = document.querySelectorAll('.stat-card .stat-value');
        if (statElements.length >= 4) {
            statElements[0].innerText = stats.totalMaterials;
            statElements[1].innerText = stats.totalNotes;
            statElements[2].innerText = stats.totalQuizzes;
            statElements[3].innerText = Math.round(stats.averageScore) + '%';
        }

        // 2. Fetch Quiz History for Chart
        const history = await api.getQuizHistory();
        const chartData = history.slice(0, 6).reverse(); // Last 6 quizzes

        const labels = chartData.map((q, i) => `Quiz ${i + 1}`);
        const scores = chartData.map(q => (q.score / q.total) * 100);

        const ctx = document.getElementById('performanceChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels.length > 0 ? labels : ['No Data'],
                    datasets: [{
                        label: 'Quiz Score (%)',
                        data: scores.length > 0 ? scores : [0],
                        borderColor: '#4f46e5',
                        backgroundColor: 'rgba(79, 70, 229, 0.1)',
                        tension: 0.4,
                        fill: true,
                        pointBackgroundColor: '#4f46e5',
                        pointBorderColor: '#fff',
                        pointBorderWidth: 2,
                        pointRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            max: 100,
                            grid: {
                                color: 'rgba(0, 0, 0, 0.05)'
                            }
                        },
                        x: {
                            grid: {
                                display: false
                            }
                        }
                    }
                }
            });
        }

        // User info is now handled by components.js

    } catch (error) {
        console.error('Dashboard init failed:', error);
    }
}

document.addEventListener('DOMContentLoaded', initDashboard);
