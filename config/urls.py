"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('warehouses/', include('warehouse.urls')),
    path('products/', include('catalog.urls')),
    path('suppliers/', include('partners.urls')),
    path('purchase-orders/', include('purchasing.urls')),
    path('grn/', include('receiving.urls')),
    path('qc/', include('quality.urls')),
    path('inventory/', include('inventory.urls')),
    path('gin/', include('shipping.urls')),
    path('stocktake/', include('stocktake.urls')),
    path('reports/', include('reports.urls')),
]

if settings.DEBUG:
    # Dev only — production serve /media/ qua webserver (nginx/...), không qua Django.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
