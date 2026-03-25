from celery import shared_task
from .models import NFe
from .api_client import add_chave, baixar_pdf, baixar_xml

@shared_task(bind=True)
def processar_chave(self, nfe_id):
    nfe = NFe.objects.get(id=nfe_id)
    try:
        # 1. Adicionar a chave
        resp = add_chave(nfe.chave_acesso)
        nfe.status = 'PROCESSING'
        nfe.tipo = resp.get('type', 'NFe')
        nfe.save()

        # 2. Tentar baixar PDF e XML
        pdf_data = baixar_pdf(nfe.chave_acesso)
        xml_data = baixar_xml(nfe.chave_acesso)
        if pdf_data and pdf_data.get('data'):
            nfe.status = 'OK'
            nfe.pdf_base64 = pdf_data['data']
            if xml_data and xml_data.get('data'):
                nfe.xml_text = xml_data['data']
                nfe.mensagem = 'PDF e XML disponíveis'
            else:
                nfe.mensagem = 'PDF disponível'
            nfe.save()
        else:
            nfe.mensagem = 'Processando, aguarde...'
            nfe.save()
    except Exception as e:
        nfe.status = 'ERROR'
        nfe.mensagem = str(e)
        nfe.save()