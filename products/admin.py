from django.contrib import admin
from .models import Category, Product, InventoryMovement, StockLoss, PriceLog, Purchase, PurchaseDetail

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'category', 'price', 'stock', 'min_stock', 'expiry_date')
    list_filter = ('category', 'supplier')
    search_fields = ('sku', 'name', 'description')
    ordering = ('name',)

@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'date', 'user')
    list_filter = ('movement_type', 'date')

@admin.register(StockLoss)
class StockLossAdmin(admin.ModelAdmin):
    list_display = ('product', 'quantity', 'reason', 'date', 'user')
    list_filter = ('reason', 'date')

@admin.register(PriceLog)
class PriceLogAdmin(admin.ModelAdmin):
    list_display = ('product', 'old_price', 'new_price', 'date', 'user')

class PurchaseDetailInline(admin.TabularInline):
    model = PurchaseDetail
    extra = 1

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'invoice_number', 'total_amount', 'date', 'user')
    list_filter = ('date', 'supplier')
    inlines = [PurchaseDetailInline]
