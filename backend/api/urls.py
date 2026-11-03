from django.urls import path
from api import views

urlpatterns = [
    # Auth
    path('auth/register', views.register_user, name='auth_register'),
    path('auth/login', views.login_user, name='auth_login'),
    
    # Dashboard
    path('dashboard/stats', views.get_dashboard_stats, name='dashboard_stats'),
    path('dashboard/analytics', views.get_performance_analytics, name='dashboard_analytics'),
    
    # Materials
    path('materials/upload', views.upload_material, name='upload_material'),
    path('materials', views.list_user_materials, name='list_user_materials'),
    path('materials/<int:material_id>/content', views.get_material_content, name='get_material_content'),
    
    # Quiz
    path('quiz/save', views.save_quiz_result, name='save_quiz_result'),
    path('quiz/history', views.get_quiz_history, name='get_quiz_history'),
    
    # Admin
    path('admin/stats', views.get_admin_stats, name='admin_stats'),
    path('admin/users', views.get_all_users, name='admin_users'),
    path('admin/users/<int:id>', views.delete_user, name='admin_delete_user'),
    path('admin/content', views.get_all_content, name='admin_content'),
    path('admin/content/<int:id>', views.delete_content, name='admin_delete_content'),
    path('admin/mcqs', views.get_all_mcqs, name='admin_mcqs'),
    path('admin/mcqs/<int:id>', views.delete_mcq, name='admin_delete_mcq'),
    path('admin/analytics/detailed', views.get_detailed_analytics, name='admin_detailed_analytics'),
    path('admin/logs', views.get_system_logs, name='admin_system_logs'),
]
