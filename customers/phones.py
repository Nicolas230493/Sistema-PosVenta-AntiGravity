"""Teléfonos de clientes en el formato internacional que exige wa.me (Argentina: 54 9 + área + número)."""

AR_COUNTRY = '54'
AR_MOBILE = '9'
AR_NATIONAL_LENGTH = 10  # código de área + número, sin 0 ni 15


def whatsapp_number(phone):
    """
    '11 5555-1234', '011 15 5555-1234', '+54 9 11 5555-1234' o '0351 15 123-4567'
    -> '5491155551234' / '5493511234567'. Devuelve '' si no alcanza para un número válido.
    """
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if digits.startswith(AR_COUNTRY) and len(digits) > AR_NATIONAL_LENGTH:
        national = digits[len(AR_COUNTRY):]
        if national.startswith(AR_MOBILE) and len(national) == AR_NATIONAL_LENGTH + 1:
            national = national[1:]
    else:
        national = digits
    national = national.lstrip('0')  # prefijo de larga distancia

    # El "15" de los celulares va después del código de área (2, 3 o 4 dígitos) y wa.me no lo usa
    if len(national) == AR_NATIONAL_LENGTH + 2:
        for area_length in (2, 3, 4):
            if national[area_length:area_length + 2] == '15':
                national = national[:area_length] + national[area_length + 2:]
                break

    # Los códigos de área argentinos empiezan con 11, 2 o 3 (sin área, "15 5555-1234" no es válido)
    if len(national) != AR_NATIONAL_LENGTH or not national.startswith(('11', '2', '3')):
        return ''
    return AR_COUNTRY + AR_MOBILE + national
