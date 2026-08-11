"""Bot de Telegram: la unica salida del sistema hacia el operador.

Este `__init__` se deja deliberadamente sin importaciones. `servicios.digest`
necesita `telegram.digest` para formatear el mensaje, y `telegram.handlers`
necesita `servicios.digest` para generarlo; importar `bot` aqui cerraba ese
ciclo y rompia la carga del paquete.
"""
