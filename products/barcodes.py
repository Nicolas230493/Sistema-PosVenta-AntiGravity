"""
Lectura de códigos de barras: código del producto, SKU y etiquetas de balanza.

Etiqueta de balanza (EAN-13 de uso interno, prefijos 20 a 29):
    2X  PPPPP  VVVVV  C
    │   │      │      └ dígito verificador EAN-13
    │   │      └ valor: peso en gramos o importe en pesos (según la Configuración)
    │   └ PLU del producto en la balanza (4, 5 o 6 dígitos; el valor ocupa el resto)
    └ prefijo 20-29
La misma lógica está en static/js/barcode-tools.js para que el POS la use sin conexión.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from core.models import ConfiguracionSistema

from .models import Product

THREE_DECIMALS = Decimal('0.001')


def ean13_check_digit(first12):
    """Dígito verificador EAN-13 para los primeros 12 dígitos."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return (10 - total % 10) % 10


def is_valid_ean13(code):
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == int(code[12])


@dataclass(frozen=True)
class ScaleLabel:
    plu: int
    value: int  # gramos o pesos, según `mode`
    mode: str


def parse_scale_barcode(code, plu_digits=5, mode=ConfiguracionSistema.BALANZA_PESO):
    """Decodifica una etiqueta de balanza. Devuelve None si el código no tiene ese formato."""
    code = (code or '').strip()
    if not (is_valid_ean13(code) and code[0] == '2') or plu_digits not in (4, 5, 6):
        return None
    plu = int(code[2:2 + plu_digits])
    value = int(code[2 + plu_digits:12])
    return ScaleLabel(plu=plu, value=value, mode=mode)


def scale_quantity(product, label):
    """Cantidad que representa la etiqueta: kg (o unidades) para `product`."""
    if label.mode == ConfiguracionSistema.BALANZA_IMPORTE:
        if not product.price:
            return None
        return (Decimal(label.value) / product.price).quantize(THREE_DECIMALS, rounding=ROUND_HALF_UP)
    if product.is_fractional:
        return (Decimal(label.value) / 1000).quantize(THREE_DECIMALS)
    return Decimal(label.value)


def resolve_code(code, config=None):
    """
    Busca el producto de un código escaneado.
    Orden: código de barras exacto → SKU → etiqueta de balanza (por PLU).
    Devuelve {'product', 'quantity', 'source'} o None. `quantity` es None salvo en etiquetas de balanza.
    """
    code = (code or '').strip()
    if not code:
        return None
    product = Product.objects.filter(Q(barcode=code) | Q(sku=code), active=True).first()
    if product:
        source = 'barcode' if product.barcode == code else 'sku'
        return {'product': product, 'quantity': None, 'source': source}

    config = config or ConfiguracionSistema.get_config()
    label = parse_scale_barcode(code, config.balanza_digitos_plu, config.balanza_formato)
    if label is None:
        return None
    product = Product.objects.filter(plu=label.plu, active=True).first()
    if product is None:
        return None
    return {'product': product, 'quantity': scale_quantity(product, label), 'source': 'balanza'}
