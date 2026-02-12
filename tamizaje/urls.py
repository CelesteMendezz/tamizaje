from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.conf import settings
from django.conf.urls.static import static
from usuarios import views as usuarios_views
from dashboard import views as dashboard_views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),

    path('usuarios/', include('usuarios.urls')),  # aquí se quedará registro + invites, etc.

    path('', lambda request: redirect('login')),

    path('dashboard/', include('dashboard.urls')),
    path('dashboard/admin/', dashboard_views.dashboard_admin, name='dashboard_admin'),
    path('dashboard/psicologo/', dashboard_views.dashboard_psicologo, name='dashboard_psicologo'),
    path('dashboard/usuario/', dashboard_views.dashboard_usuario, name='dashboard_usuario'),

    path('dashboard/admin/catalogo/', include(('catalogo.urls', 'catalogo'), namespace='catalogo')),

    # ✅ ÚNICO login/logout global
    path('login/', usuarios_views.custom_login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # Password reset
    path('accounts/password_reset/',
         auth_views.PasswordResetView.as_view(template_name='registration/password_reset_form.html'),
         name='password_reset'),
    path('accounts/password_reset/done/',
         auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'),
         name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'),
         name='password_reset_confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'),
         name='password_reset_complete'),

    path('cuentas/', include('django.contrib.auth.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
