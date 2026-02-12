# usuarios/urls.py
from django.urls import path
from django.contrib.auth.views import LogoutView
from .views import registro_usuario
from .views import registro_usuario, api_invites_list, api_invites_create, api_invites_revoke
from . import views

app_name = 'usuarios'


urlpatterns = [
       
    path('registro/', registro_usuario, name='registro'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),  
    path('api/invites/', api_invites_list, name='api_invites_list'),
    path('api/invites/create/', api_invites_create, name='api_invites_create'),
    path('api/invites/<int:pk>/revoke/', views.api_invites_revoke, name='api_invites_revoke'),
    path('disabled/', views.account_disabled_view, name='account_disabled'),
]
