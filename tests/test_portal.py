from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.modulos import pasarela_simulada
from escenarios import PICKER, nuevo_cliente
from escenarios_c3 import tienda_con_productos, pedido_pagado, cancelar

client = TestClient(app)

@pytest.fixture(autouse=True)
def demo(monkeypatch):
    monkeypatch.setenv('EXPRESSYA_DEMO','1')
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def test_catalogo_publico_no_expone_saldos():
    tienda, productos = tienda_con_productos(1)
    r=client.get(f'/v1/portal/catalogo/{tienda}')
    assert r.status_code==200
    assert r.json()[0]['precio']=='100.00'
    assert not {'e','n','r','c','d'}.intersection(r.json()[0])


def test_pedido_y_historial_exigen_propietario_o_rol_en_tienda():
    tienda, productos=tienda_con_productos(1)
    pedido, cliente, _=pedido_pagado(tienda,[(productos[0],1)])
    url=f'/v1/portal/pedido/{pedido}'
    assert client.get(url,headers={'Cliente-Id':str(nuevo_cliente())}).status_code==404
    assert client.get(url,headers={'Usuario-Id':str(uuid4())}).status_code==403
    assert client.get(url,headers={'Cliente-Id':str(cliente)}).json()['pagos'][0]['estado']=='APROBADO'
    assert client.get(f'/v1/portal/movimientos/{tienda}/{productos[0]}',headers={'Usuario-Id':str(uuid4())}).status_code==403
    assert client.get(f'/v1/portal/operacion/{tienda}',headers={'Usuario-Id':str(PICKER)}).status_code==200


def test_cl08_real_impide_cancelacion_y_se_refleja_en_portal():
    tienda, productos=tienda_con_productos(1,unidades=5)
    pedido, cliente, _=pedido_pagado(tienda,[(productos[0],2)])
    detalle=client.get(f'/v1/portal/pedido/{pedido}',headers={'Cliente-Id':str(cliente)}).json()
    linea=detalle['lineas'][0]['linea_id']
    r=client.post(f'/v1/lineas-pedido/{linea}/separacion',json={'cantidad':1},headers={'Usuario-Id':str(PICKER),'Clave-Operacion':str(uuid4())})
    assert r.status_code==200,r.text
    r=cancelar(pedido,cliente)
    assert r.status_code==409 and r.json()['codigo']=='PREPARACION_INICIADA'
    detalle=client.get(f'/v1/portal/pedido/{pedido}',headers={'Cliente-Id':str(cliente)}).json()
    assert detalle['lineas'][0]['s']==1
    inv=client.get(f'/v1/portal/operacion/{tienda}',headers={'Usuario-Id':str(PICKER)}).json()['inventario'][0]
    assert (inv['e'],inv['c'],inv['d'],inv['e_historial'])==(4,1,3,4)


def test_cancelacion_reembolso_y_aviso_visibles():
    tienda, productos=tienda_con_productos(1)
    pedido, cliente, _=pedido_pagado(tienda,[(productos[0],2)])
    assert cancelar(pedido,cliente).status_code==200
    p=client.get(f'/v1/portal/pedido/{pedido}',headers={'Cliente-Id':str(cliente)}).json()
    assert p['estado']=='CANCELADO'
    assert p['reembolsos'][0]['monto']=='200.00'
    assert p['reembolsos'][0]['fecha_confirmacion']
    assert p['avisos'][0]['tipo']=='PEDIDO_CANCELADO'


def test_ni_en_demo_hay_altas_anonimas_ni_selector_de_personal():
    assert client.post('/v1/portal/clientes').status_code in (404, 405)
    assert client.get('/v1/portal/personal').status_code in (404, 405)


def test_pendientes_de_la_tienda_muestran_conteos_danos_e_inspecciones():
    from escenarios_faltantes import contar, nuevo_usuario
    from escenarios_danos import danar, inspeccionar

    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, 'PICKER')
    personal = nuevo_usuario(tienda, 'PERSONAL_TIENDA')
    inspector = nuevo_usuario(tienda, 'INSPECTOR')
    assert contar(tienda, producto, picker, 9).status_code == 201
    dano = danar(tienda, producto, personal, 2).json()['movimiento_id']
    inspeccionar(dano, inspector, 1, 'APTA')

    r = client.get(f'/v1/portal/pendientes/{tienda}', headers={'Usuario-Id': str(picker)})

    assert r.status_code == 200, r.text
    p = r.json()
    assert [(c['cantidad_contada'], c['e_al_corte']) for c in p['conteos']] == [(9, 10)]
    assert [(d['origen'], d['estado']) for d in p['discrepancias']] == [('CONTEO', 'EN_VALIDACION')]
    assert [(d['cantidad'], d['por_inspeccionar']) for d in p['danos']] == [(2, 1)]
    assert [(i['resultado'], i['resuelta']) for i in p['inspecciones']] == [('APTA', False)]
    assert p['avisos'] == []
    assert client.get(f'/v1/portal/pendientes/{tienda}', headers={'Usuario-Id': str(uuid4())}).status_code == 403
