from django.urls import path, include, re_path
from api.views import serve_frontend

urlpatterns = [
    # Include all API paths under api/v1
    path('api/v1/', include('api.urls')),
    
    # Catch-all to serve frontend static files
    re_path(r'^(?P<path>.*)$', serve_frontend, name='frontend'),
]
