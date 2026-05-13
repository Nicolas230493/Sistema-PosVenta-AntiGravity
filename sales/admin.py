from django.contrib import admin
from .models import Sale, SaleDetail, SaleReturn, SaleReturnDetail, Promotion

@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ('name', 'promo_type', 'start_date', 'end_date', 'active')
    list_filter = ('promo_type', 'active', 'start_date')
    search_fields = ('name',)
    filter_horizontal = ('products', 'categories')

class SaleDetailInline(admin.TabularInline):
    model = SaleDetail
    extra = 0
    readonly_fields = ('subtotal',)

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'date', 'total_amount', 'payment_method', 'user')
    list_filter = ('date', 'payment_method')
    search_fields = ('id', 'customer__full_name')
    inlines = [SaleDetailInline]

class SaleReturnDetailInline(admin.TabularInline):
    model = SaleReturnDetail
    extra = 0

@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale', 'date', 'total_amount', 'user')
    list_filter = ('date',)
    inlines = [SaleReturnDetailInline]
