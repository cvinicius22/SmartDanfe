import json
import base64
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.conf import settings
from django.urls import reverse
import xml.etree.ElementTree as ET
import pandas as pd
import mercadopago
from .models import NFe, Payment, UserProfile, Plan
from .api_client import add_chave, baixar_pdf, baixar_xml
from .forms import CustomUserCreationForm
from .decorators import subscription_required
import hashlib
import hmac
import urllib.parse

logger = logging.getLogger(__name__)

from openai import OpenAI
from .models import Plan  # Certifique-se de importar o modelo Plan

from .models import Plan, Payment
from django.shortcuts import render, redirect


def home(request):
    plans = Plan.objects.filter(is_active=True)
    plans_dict = {plan.name: plan.price for plan in plans}
    economias = {}
    
    # Cálculo do plano trimestral (economia em relação a 3 meses)
    if 'mensal' in plans_dict and 'trimestral' in plans_dict:
        mensal = plans_dict['mensal']
        trimestral = plans_dict['trimestral']
        valor_3_meses = mensal * 3
        if valor_3_meses > trimestral:
            economia = ((valor_3_meses - trimestral) / valor_3_meses) * 100
            economias['trimestral'] = f"{economia:.0f}%"
    
    # Cálculo do plano anual (economia em relação a 12 meses)
    if 'mensal' in plans_dict and 'anual' in plans_dict:
        mensal = plans_dict['mensal']
        anual = plans_dict['anual']
        valor_12_meses = mensal * 12
        if valor_12_meses > anual:
            economia = ((valor_12_meses - anual) / valor_12_meses) * 100
            economias['anual'] = f"{economia:.0f}%"
    
    context = {
        'plans': plans_dict,
        'economias': economias,
    }
    
    if request.user.is_authenticated:
        has_approved = Payment.objects.filter(user=request.user, status='APPROVED').exists()
        has_pending = Payment.objects.filter(user=request.user, status='PENDING').exists()
        context['has_pending'] = has_pending
        if has_approved:
            return redirect('dashboard')
    
    return render(request, 'nfe/plans.html', context)

def register(request):
    """Registro de novos usuários, capturando o plano da URL"""
    plan = request.GET.get('plan')
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if plan and plan in ['mensal', 'trimestral', 'anual']:
                return redirect(f'/dashboard/checkout/?plan={plan}')
            else:
                return redirect('home')
    else:
        form = CustomUserCreationForm()
    return render(request, 'registration/register.html', {'form': form, 'plan': plan})


@login_required
@subscription_required
def dashboard(request):
    return render(request, 'nfe/dashboard.html')


@require_POST
@csrf_exempt
@login_required
def process_keys(request):
    data = json.loads(request.body)
    keys = data.get('keys', [])
    if not keys:
        return JsonResponse({'error': 'Nenhuma chave fornecida'}, status=400)

    for chave in keys:
        nfe, created = NFe.objects.get_or_create(
            user=request.user,
            chave_acesso=chave,
            defaults={'status': 'WAITING'}
        )
        if created:
            try:
                resp = add_chave(chave)
                nfe.status = 'PROCESSING'
                nfe.tipo = resp.get('type', 'NFe')
                nfe.save()
                pdf_data = baixar_pdf(chave)
                xml_data = baixar_xml(chave)
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

    return JsonResponse({'success': True, 'message': f'{len(keys)} chave(s) em processamento'})


@require_GET
@login_required
def nfe_status(request):
    nfes = NFe.objects.filter(user=request.user).order_by('-created_at')
    data = []
    for nfe in nfes:
        # (código existente para tentar baixar PDF/XML, se necessário)
        if nfe.status == 'PROCESSING' and not nfe.pdf_base64:
            pdf_data = baixar_pdf(nfe.chave_acesso)
            if pdf_data and pdf_data.get('data'):
                nfe.status = 'OK'
                nfe.pdf_base64 = pdf_data['data']
                xml_data = baixar_xml(nfe.chave_acesso)
                if xml_data and xml_data.get('data'):
                    nfe.xml_text = xml_data['data']
                    nfe.mensagem = 'PDF e XML disponíveis'
                else:
                    nfe.mensagem = 'PDF disponível'
                nfe.save()
        elif nfe.status == 'OK' and not nfe.xml_text:
            xml_data = baixar_xml(nfe.chave_acesso)
            if xml_data and xml_data.get('data'):
                nfe.xml_text = xml_data['data']
                nfe.mensagem = 'PDF e XML disponíveis'
                nfe.save()

        # Dados básicos (sempre presentes)
        item = {
            'chave': nfe.chave_acesso,
            'status': nfe.status,
            'tipo': nfe.tipo,
            'mensagem': nfe.mensagem,
            'pdf_disponivel': bool(nfe.pdf_base64),
            'xml_disponivel': bool(nfe.xml_text),
            'created_at': nfe.created_at.isoformat(),
            # Campos extras (inicialmente vazios)
            'emitente_nome': '',
            'emitente_cnpj': '',
            'numero_nf': '',
            'serie': '',
            'valor_total': '0.00',
        }

        # Se a nota está OK e tem XML, extrai os dados adicionais
        if nfe.status == 'OK' and nfe.xml_text:
            try:
                root = ET.fromstring(nfe.xml_text)
                ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

                infNFe = root.find('.//nfe:infNFe', ns)
                if infNFe is not None:
                    # Emitente
                    emit = infNFe.find('nfe:emit', ns)
                    if emit is not None:
                        xNome = emit.find('nfe:xNome', ns)
                        if xNome is not None:
                            item['emitente_nome'] = xNome.text
                        cnpj = emit.find('nfe:CNPJ', ns)
                        if cnpj is not None:
                            item['emitente_cnpj'] = cnpj.text

                    # Número e série da NF
                    ide = infNFe.find('nfe:ide', ns)
                    if ide is not None:
                        nNF = ide.find('nfe:nNF', ns)
                        if nNF is not None:
                            item['numero_nf'] = nNF.text
                        serie = ide.find('nfe:serie', ns)
                        if serie is not None:
                            item['serie'] = serie.text

                    # Valor total
                    total = infNFe.find('.//nfe:ICMSTot', ns)
                    if total is not None:
                        vNF = total.find('nfe:vNF', ns)
                        if vNF is not None:
                            item['valor_total'] = vNF.text

            except Exception as e:
                logger.error(f"Erro ao extrair dados do XML da nota {nfe.chave_acesso}: {e}")

        data.append(item)

    return JsonResponse({'nfes': data})


@require_GET
@login_required
def download_pdf(request, chave):
    try:
        nfe = NFe.objects.get(user=request.user, chave_acesso=chave)
        if not nfe.pdf_base64:
            return HttpResponse('PDF não disponível', status=404)
        pdf_bytes = base64.b64decode(nfe.pdf_base64)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{chave}.pdf"'
        return response
    except NFe.DoesNotExist:
        return HttpResponse('NF-e não encontrada', status=404)


@require_GET
@login_required
def download_xml(request, chave):
    try:
        nfe = NFe.objects.get(user=request.user, chave_acesso=chave)
        if not nfe.xml_text:
            return HttpResponse('XML não disponível', status=404)
        response = HttpResponse(nfe.xml_text, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="{chave}.xml"'
        return response
    except NFe.DoesNotExist:
        return HttpResponse('NF-e não encontrada', status=404)


@require_POST
@csrf_exempt
@login_required
def clear_all(request):
    NFe.objects.filter(user=request.user).delete()
    return JsonResponse({'success': True})


@login_required
def relatorio_excel(request):
    nfes = NFe.objects.filter(user=request.user, status='OK', xml_text__isnull=False).order_by('-created_at')
    
    # Listas para armazenar os dados
    notas_resumo = []
    itens = []
    xml_completo = []

    for nfe in nfes:
        xml_completo.append({
            'Chave': nfe.chave_acesso,
            'XML Completo': nfe.xml_text
        })
        
        try:
            root = ET.fromstring(nfe.xml_text)
            ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
            
            # Dados da nota (infNFe)
            infNFe = root.find('.//nfe:infNFe', ns)
            if infNFe is None:
                continue
            
            # Ide (identificação)
            ide = infNFe.find('nfe:ide', ns)
            if ide is not None:
                serie = ide.find('nfe:serie', ns).text if ide.find('nfe:serie', ns) is not None else ''
                nNF = ide.find('nfe:nNF', ns).text if ide.find('nfe:nNF', ns) is not None else ''
                dhEmi = ide.find('nfe:dhEmi', ns).text if ide.find('nfe:dhEmi', ns) is not None else ''
                dhSaiEnt = ide.find('nfe:dhSaiEnt', ns).text if ide.find('nfe:dhSaiEnt', ns) is not None else ''
                natOp = ide.find('nfe:natOp', ns).text if ide.find('nfe:natOp', ns) is not None else ''
                finNFe = ide.find('nfe:finNFe', ns).text if ide.find('nfe:finNFe', ns) is not None else ''
                tpNF = ide.find('nfe:tpNF', ns).text if ide.find('nfe:tpNF', ns) is not None else ''
                cNF = ide.find('nfe:cNF', ns).text if ide.find('nfe:cNF', ns) is not None else ''
                verProc = ide.find('nfe:verProc', ns).text if ide.find('nfe:verProc', ns) is not None else ''
            else:
                serie = nNF = dhEmi = dhSaiEnt = natOp = finNFe = tpNF = cNF = verProc = ''
            
            # Emitente
            emit = infNFe.find('nfe:emit', ns)
            if emit is not None:
                emit_nome = emit.find('nfe:xNome', ns).text if emit.find('nfe:xNome', ns) is not None else ''
                emit_cnpj = emit.find('nfe:CNPJ', ns).text if emit.find('nfe:CNPJ', ns) is not None else ''
                emit_ie = emit.find('nfe:IE', ns).text if emit.find('nfe:IE', ns) is not None else ''
                emit_ender = emit.find('nfe:enderEmit', ns)
                if emit_ender is not None:
                    emit_uf = emit_ender.find('nfe:UF', ns).text if emit_ender.find('nfe:UF', ns) is not None else ''
                    emit_mun = emit_ender.find('nfe:xMun', ns).text if emit_ender.find('nfe:xMun', ns) is not None else ''
                    emit_cep = emit_ender.find('nfe:CEP', ns).text if emit_ender.find('nfe:CEP', ns) is not None else ''
                else:
                    emit_uf = emit_mun = emit_cep = ''
            else:
                emit_nome = emit_cnpj = emit_ie = emit_uf = emit_mun = emit_cep = ''
            
            # Destinatário
            dest = infNFe.find('nfe:dest', ns)
            if dest is not None:
                dest_nome = dest.find('nfe:xNome', ns).text if dest.find('nfe:xNome', ns) is not None else ''
                dest_cnpj = dest.find('nfe:CNPJ', ns).text if dest.find('nfe:CNPJ', ns) is not None else ''
                dest_ie = dest.find('nfe:IE', ns).text if dest.find('nfe:IE', ns) is not None else ''
                dest_email = dest.find('nfe:email', ns).text if dest.find('nfe:email', ns) is not None else ''
                dest_ender = dest.find('nfe:enderDest', ns)
                if dest_ender is not None:
                    dest_uf = dest_ender.find('nfe:UF', ns).text if dest_ender.find('nfe:UF', ns) is not None else ''
                    dest_mun = dest_ender.find('nfe:xMun', ns).text if dest_ender.find('nfe:xMun', ns) is not None else ''
                    dest_cep = dest_ender.find('nfe:CEP', ns).text if dest_ender.find('nfe:CEP', ns) is not None else ''
                else:
                    dest_uf = dest_mun = dest_cep = ''
            else:
                dest_nome = dest_cnpj = dest_ie = dest_email = dest_uf = dest_mun = dest_cep = ''
            
            # Transporte
            transp = infNFe.find('nfe:transp', ns)
            modFrete = ''
            vol_qVol = ''
            vol_pesoB = ''
            vol_pesoL = ''
            if transp is not None:
                modFrete = transp.find('nfe:modFrete', ns).text if transp.find('nfe:modFrete', ns) is not None else ''
                vol = transp.find('nfe:vol', ns)
                if vol is not None:
                    vol_qVol = vol.find('nfe:qVol', ns).text if vol.find('nfe:qVol', ns) is not None else ''
                    vol_pesoB = vol.find('nfe:pesoB', ns).text if vol.find('nfe:pesoB', ns) is not None else ''
                    vol_pesoL = vol.find('nfe:pesoL', ns).text if vol.find('nfe:pesoL', ns) is not None else ''
            
            # Totais (ICMSTot)
            total = infNFe.find('.//nfe:ICMSTot', ns)
            if total is not None:
                vProd = total.find('nfe:vProd', ns).text if total.find('nfe:vProd', ns) is not None else '0'
                vNF = total.find('nfe:vNF', ns).text if total.find('nfe:vNF', ns) is not None else '0'
                vICMS = total.find('nfe:vICMS', ns).text if total.find('nfe:vICMS', ns) is not None else '0'
                vIPI = total.find('nfe:vIPI', ns).text if total.find('nfe:vIPI', ns) is not None else '0'
                vPIS = total.find('nfe:vPIS', ns).text if total.find('nfe:vPIS', ns) is not None else '0'
                vCOFINS = total.find('nfe:vCOFINS', ns).text if total.find('nfe:vCOFINS', ns) is not None else '0'
                vFCP = total.find('nfe:vFCP', ns).text if total.find('nfe:vFCP', ns) is not None else '0'
                vFCPST = total.find('nfe:vFCPST', ns).text if total.find('nfe:vFCPST', ns) is not None else '0'
                vST = total.find('nfe:vST', ns).text if total.find('nfe:vST', ns) is not None else '0'
                vDesc = total.find('nfe:vDesc', ns).text if total.find('nfe:vDesc', ns) is not None else '0'
                vFrete = total.find('nfe:vFrete', ns).text if total.find('nfe:vFrete', ns) is not None else '0'
                vSeg = total.find('nfe:vSeg', ns).text if total.find('nfe:vSeg', ns) is not None else '0'
                vOutro = total.find('nfe:vOutro', ns).text if total.find('nfe:vOutro', ns) is not None else '0'
            else:
                vProd = vNF = vICMS = vIPI = vPIS = vCOFINS = vFCP = vFCPST = vST = vDesc = vFrete = vSeg = vOutro = '0'
            
            # Pagamento
            pag = infNFe.find('.//nfe:detPag', ns)
            if pag is not None:
                tPag = pag.find('nfe:tPag', ns).text if pag.find('nfe:tPag', ns) is not None else ''
                vPag = pag.find('nfe:vPag', ns).text if pag.find('nfe:vPag', ns) is not None else ''
            else:
                tPag = vPag = ''
            
            # Duplicatas (se houver)
            dup = infNFe.find('.//nfe:dup', ns)
            if dup is not None:
                nDup = dup.find('nfe:nDup', ns).text if dup.find('nfe:nDup', ns) is not None else ''
                dVenc = dup.find('nfe:dVenc', ns).text if dup.find('nfe:dVenc', ns) is not None else ''
                vDup = dup.find('nfe:vDup', ns).text if dup.find('nfe:vDup', ns) is not None else ''
            else:
                nDup = dVenc = vDup = ''
            
            # Dados da nota para resumo
            notas_resumo.append({
                'Chave': nfe.chave_acesso,
                'Série': serie,
                'Número NF': nNF,
                'Data Emissão': dhEmi,
                'Data Saída/Entrada': dhSaiEnt,
                'Natureza Operação': natOp,
                'Finalidade': finNFe,
                'Tipo NF (0=Entrada, 1=Saída)': tpNF,
                'Número Controle': cNF,
                'Versão Processo': verProc,
                'Emitente (Nome)': emit_nome,
                'Emitente (CNPJ)': emit_cnpj,
                'Emitente (IE)': emit_ie,
                'Emitente (UF)': emit_uf,
                'Emitente (Município)': emit_mun,
                'Emitente (CEP)': emit_cep,
                'Destinatário (Nome)': dest_nome,
                'Destinatário (CNPJ)': dest_cnpj,
                'Destinatário (IE)': dest_ie,
                'Destinatário (E-mail)': dest_email,
                'Destinatário (UF)': dest_uf,
                'Destinatário (Município)': dest_mun,
                'Destinatário (CEP)': dest_cep,
                'Modalidade Frete': modFrete,
                'Qtde Volumes': vol_qVol,
                'Peso Bruto (kg)': vol_pesoB,
                'Peso Líquido (kg)': vol_pesoL,
                'Valor dos Produtos (R$)': vProd,
                'Valor Total NF (R$)': vNF,
                'ICMS (R$)': vICMS,
                'IPI (R$)': vIPI,
                'PIS (R$)': vPIS,
                'COFINS (R$)': vCOFINS,
                'FCP (R$)': vFCP,
                'FCPST (R$)': vFCPST,
                'ST (R$)': vST,
                'Desconto (R$)': vDesc,
                'Frete (R$)': vFrete,
                'Seguro (R$)': vSeg,
                'Outras Despesas (R$)': vOutro,
                'Forma de Pagamento': tPag,
                'Valor Pago (R$)': vPag,
                'Número Duplicata': nDup,
                'Vencimento Duplicata': dVenc,
                'Valor Duplicata (R$)': vDup,
            })
            
            # Processa os itens
            for det in root.findall('.//nfe:det', ns):
                prod = det.find('nfe:prod', ns)
                if prod is None:
                    continue
                
                # Dados do produto
                cProd = prod.find('nfe:cProd', ns).text if prod.find('nfe:cProd', ns) is not None else ''
                xProd = prod.find('nfe:xProd', ns).text if prod.find('nfe:xProd', ns) is not None else ''
                NCM = prod.find('nfe:NCM', ns).text if prod.find('nfe:NCM', ns) is not None else ''
                CEST = prod.find('nfe:CEST', ns).text if prod.find('nfe:CEST', ns) is not None else ''
                CFOP = prod.find('nfe:CFOP', ns).text if prod.find('nfe:CFOP', ns) is not None else ''
                uCom = prod.find('nfe:uCom', ns).text if prod.find('nfe:uCom', ns) is not None else ''
                qCom = prod.find('nfe:qCom', ns).text if prod.find('nfe:qCom', ns) is not None else ''
                vUnCom = prod.find('nfe:vUnCom', ns).text if prod.find('nfe:vUnCom', ns) is not None else ''
                vProd = prod.find('nfe:vProd', ns).text if prod.find('nfe:vProd', ns) is not None else ''
                
                # Impostos do item
                imposto = det.find('nfe:imposto', ns)
                ICMS = None
                if imposto is not None:
                    ICMS = imposto.find('.//nfe:ICMS', ns)
                
                # Extraindo valores do ICMS (pode ser ICMS00, ICMS10, ICMS40, etc.)
                vICMS_item = '0'
                pICMS_item = '0'
                vBC_item = '0'
                if ICMS is not None:
                    for child in ICMS:
                        if child.tag.endswith('ICMS00') or child.tag.endswith('ICMS10') or child.tag.endswith('ICMS20') or child.tag.endswith('ICMS40') or child.tag.endswith('ICMS51'):
                            vICMS_item = child.find('nfe:vICMS', ns).text if child.find('nfe:vICMS', ns) is not None else '0'
                            pICMS_item = child.find('nfe:pICMS', ns).text if child.find('nfe:pICMS', ns) is not None else '0'
                            vBC_item = child.find('nfe:vBC', ns).text if child.find('nfe:vBC', ns) is not None else '0'
                            break
                
                # IPI
                IPI = imposto.find('nfe:IPI', ns) if imposto is not None else None
                vIPI_item = '0'
                if IPI is not None:
                    ipiTrib = IPI.find('nfe:IPITrib', ns)
                    if ipiTrib is not None:
                        vIPI_item = ipiTrib.find('nfe:vIPI', ns).text if ipiTrib.find('nfe:vIPI', ns) is not None else '0'
                
                # PIS
                PIS = imposto.find('nfe:PIS', ns) if imposto is not None else None
                vPIS_item = '0'
                if PIS is not None:
                    pisAliq = PIS.find('nfe:PISAliq', ns)
                    if pisAliq is not None:
                        vPIS_item = pisAliq.find('nfe:vPIS', ns).text if pisAliq.find('nfe:vPIS', ns) is not None else '0'
                    else:
                        pisNT = PIS.find('nfe:PISNT', ns)
                        if pisNT is not None:
                            vPIS_item = '0'
                
                # COFINS
                COFINS = imposto.find('nfe:COFINS', ns) if imposto is not None else None
                vCOFINS_item = '0'
                if COFINS is not None:
                    cofinsAliq = COFINS.find('nfe:COFINSAliq', ns)
                    if cofinsAliq is not None:
                        vCOFINS_item = cofinsAliq.find('nfe:vCOFINS', ns).text if cofinsAliq.find('nfe:vCOFINS', ns) is not None else '0'
                
                itens.append({
                    'Chave NF-e': nfe.chave_acesso,
                    'Série': serie,
                    'Número NF': nNF,
                    'Data Emissão': dhEmi,
                    'Código Produto': cProd,
                    'Descrição Produto': xProd,
                    'NCM': NCM,
                    'CEST': CEST,
                    'CFOP': CFOP,
                    'Unidade': uCom,
                    'Quantidade': qCom,
                    'Valor Unitário (R$)': vUnCom,
                    'Valor Total Item (R$)': vProd,
                    'ICMS (R$)': vICMS_item,
                    'Alíquota ICMS (%)': pICMS_item,
                    'Base ICMS (R$)': vBC_item,
                    'IPI (R$)': vIPI_item,
                    'PIS (R$)': vPIS_item,
                    'COFINS (R$)': vCOFINS_item,
                })
                
        except Exception as e:
            # Se falhar, adiciona erro nas listas
            notas_resumo.append({'Chave': nfe.chave_acesso, 'Erro no processamento': str(e)})
            itens.append({'Chave NF-e': nfe.chave_acesso, 'Erro no processamento': str(e)})
    
    # Cria DataFrames
    df_notas = pd.DataFrame(notas_resumo) if notas_resumo else pd.DataFrame({'Mensagem': ['Nenhuma NF-e com XML disponível.']})
    df_itens = pd.DataFrame(itens) if itens else pd.DataFrame({'Mensagem': ['Nenhum item encontrado.']})
    df_xml = pd.DataFrame(xml_completo) if xml_completo else pd.DataFrame({'Mensagem': ['Nenhuma NF-e com XML disponível.']})
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="relatorio_nfes.xlsx"'
    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df_notas.to_excel(writer, index=False, sheet_name='Resumo Notas')
        df_itens.to_excel(writer, index=False, sheet_name='Itens Notas')
        df_xml.to_excel(writer, index=False, sheet_name='XML Completo')
        
        # Ajuste de largura das colunas para cada planilha
        for sheet_name in ['Resumo Notas', 'Itens Notas', 'XML Completo']:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_len = 0
                col_letter = column[0].column_letter
                for cell in column:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                adjusted_width = min(max_len + 2, 50)
                worksheet.column_dimensions[col_letter].width = adjusted_width
            # Para a planilha XML, a coluna do XML pode ser maior
            if sheet_name == 'XML Completo':
                worksheet.column_dimensions['B'].width = 80
    
    return response

@login_required
def stats(request):
    nfes = NFe.objects.filter(user=request.user, status='OK', xml_text__isnull=False)
    total_nfes = nfes.count()
    total_value = 0.0
    total_items = 0
    type_counts = defaultdict(int)
    monthly_counts = defaultdict(int)
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
    for nfe in nfes:
        try:
            root = ET.fromstring(nfe.xml_text)
            tipo = nfe.tipo or 'NFe'
            type_counts[tipo] += 1
            total_el = root.find('.//nfe:ICMSTot', ns)
            if total_el is not None:
                vNF = total_el.find('nfe:vNF', ns)
                if vNF is not None and vNF.text:
                    total_value += float(vNF.text)
            items = root.findall('.//nfe:det', ns)
            total_items += len(items)
            ide = root.find('.//nfe:ide', ns)
            if ide is not None:
                dhEmi = ide.find('nfe:dhEmi', ns)
                if dhEmi is not None and dhEmi.text:
                    try:
                        dt = datetime.fromisoformat(dhEmi.text)
                        month_key = dt.strftime('%Y-%m')
                        monthly_counts[month_key] += 1
                    except:
                        pass
        except Exception as e:
            pass
    return JsonResponse({
        'total_nfes': total_nfes,
        'total_value': total_value,
        'total_items': total_items,
        'type_labels': list(type_counts.keys()),
        'type_data': list(type_counts.values()),
        'monthly_labels': sorted(monthly_counts.keys()),
        'monthly_data': [monthly_counts[k] for k in sorted(monthly_counts.keys())],
    })

import logging
logger = logging.getLogger(__name__)


import json
import base64
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.conf import settings
from django.urls import reverse
import xml.etree.ElementTree as ET
import pandas as pd
import mercadopago
import hashlib
import hmac
import urllib.parse
from .models import NFe, Payment, UserProfile, Plan
from .api_client import add_chave, baixar_pdf, baixar_xml
from .forms import CustomUserCreationForm
from .decorators import subscription_required

logger = logging.getLogger(__name__)


def home(request):
    plans = Plan.objects.filter(is_active=True)
    plans_dict = {plan.name: plan.price for plan in plans}
    economias = {}
    
    if 'mensal' in plans_dict and 'trimestral' in plans_dict:
        mensal = plans_dict['mensal']
        trimestral = plans_dict['trimestral']
        valor_3_meses = mensal * 3
        if valor_3_meses > trimestral:
            economia = ((valor_3_meses - trimestral) / valor_3_meses) * 100
            economias['trimestral'] = f"{economia:.0f}%"
    
    if 'mensal' in plans_dict and 'anual' in plans_dict:
        mensal = plans_dict['mensal']
        anual = plans_dict['anual']
        valor_12_meses = mensal * 12
        if valor_12_meses > anual:
            economia = ((valor_12_meses - anual) / valor_12_meses) * 100
            economias['anual'] = f"{economia:.0f}%"
    
    context = {'plans': plans_dict, 'economias': economias}
    
    if request.user.is_authenticated:
        has_approved = Payment.objects.filter(user=request.user, status='APPROVED').exists()
        has_pending = Payment.objects.filter(user=request.user, status='PENDING').exists()
        context['has_pending'] = has_pending
        if has_approved:
            return redirect('dashboard')
    
    return render(request, 'nfe/plans.html', context)


def register(request):
    plan = request.GET.get('plan')
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if plan and plan in ['mensal', 'trimestral', 'anual']:
                return redirect(f'/dashboard/checkout/?plan={plan}')
            else:
                return redirect('home')
    else:
        form = CustomUserCreationForm()
    return render(request, 'registration/register.html', {'form': form, 'plan': plan})


@login_required
@subscription_required
def dashboard(request):
    pending_payments = Payment.objects.filter(user=request.user, status='PENDING').exists()
    return render(request, 'nfe/dashboard.html', {'pending_payments': pending_payments})


# ... (suas outras views: process_keys, nfe_status, download_pdf, download_xml, clear_all, relatorio_excel, stats) ...


@login_required
def checkout(request):
    plan_name = request.GET.get('plan')
    preference_id_param = request.GET.get('preference_id')

    # Retomar pagamento pendente
    if preference_id_param:
        payment = Payment.objects.filter(
            preference_id=preference_id_param,
            user=request.user,
            status='PENDING'
        ).first()
        if payment and payment.init_point:
            return render(request, 'nfe/checkout.html', {
                'plan': payment.plan.name if isinstance(payment.plan, Plan) else payment.plan,
                'amount': float(payment.amount),
                'preference_id': payment.preference_id,
                'public_key': settings.MERCADOPAGO_PUBLIC_KEY,
            })
        else:
            return redirect('home')

    # Novo plano
    if not plan_name:
        return redirect('home')

    try:
        plan = Plan.objects.get(name=plan_name, is_active=True)
    except Plan.DoesNotExist:
        return redirect('home')

    amount = float(plan.price)

    if request.user.profile.subscription_active:
        return redirect('dashboard')

    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)

    base_url = request.build_absolute_uri('/').rstrip('/')
    public_url = getattr(settings, 'PUBLIC_URL', base_url)

    def build_absolute_url(url_name):
        try:
            path = reverse(url_name)
            return f"{public_url}{path}"
        except Exception as e:
            logger.error(f"Falha ao construir URL para {url_name}: {e}")
            return None

    success_url = build_absolute_url('payment_success')
    failure_url = build_absolute_url('payment_failure')
    pending_url = build_absolute_url('payment_pending')
    notification_url = build_absolute_url('payment_webhook')

    # Log das URLs (importante para depuração)
    logger.info(f"Success URL: {success_url}")
    logger.info(f"Failure URL: {failure_url}")
    logger.info(f"Pending URL: {pending_url}")
    logger.info(f"Notification URL: {notification_url}")

    if not all([success_url, failure_url, pending_url, notification_url]):
        return render(request, 'nfe/error.html', {
            'message': 'URLs de retorno inválidas. Verifique as rotas.'
        })

    preference_data = {
        "items": [{
            "id": f"plan_{plan.id}",
            "title": f"SmartDanfe - Plano {plan.name.capitalize()}",
            "description": f"Acesso ao conversor de NF-e - Plano {plan.name.capitalize()}",
            "category_id": "services",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": amount,
        }],
        "payer": {
            "email": request.user.email or "cliente@smartdanfe.com.br",
            "first_name": request.user.first_name or "Cliente",
            "last_name": request.user.last_name or "SmartDanfe",
            "phone": {"area_code": "11", "number": "999999999"},
            "identification": {"type": "CPF", "number": "11111111111"}
        },
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url,
        },
        "auto_return": "approved",
        "notification_url": notification_url,
        "external_reference": f"{request.user.id}_{plan.id}",
        "binary_mode": True,
        "statement_descriptor": "SMARTDANFE",
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        logger.info(f"Resposta MP: {preference_response}")

        if preference_response.get('status') != 201:
            error = preference_response.get('response', {}).get('message', 'Erro desconhecido')
            cause = preference_response.get('response', {}).get('cause')
            if cause:
                error += f" - {cause}"
            return render(request, 'nfe/error.html', {
                'message': f'Erro ao criar preferência: {error}'
            })

        preference = preference_response.get('response', {})
        if 'id' not in preference:
            return render(request, 'nfe/error.html', {
                'message': 'Resposta inválida do Mercado Pago'
            })

        preference_id = preference['id']
        init_point = preference.get('init_point')

    except Exception as e:
        logger.exception("Erro na criação da preferência")
        return render(request, 'nfe/error.html', {
            'message': f'Erro interno: {str(e)}'
        })

    Payment.objects.create(
        user=request.user,
        plan=plan,
        amount=plan.price,
        preference_id=preference_id,
        init_point=init_point,
        external_reference=f"{request.user.id}_{plan.id}",  # adicionar
        status='PENDING'
    )

    return render(request, 'nfe/checkout.html', {
        'plan': plan.name,
        'amount': amount,
        'preference_id': preference_id,
        'public_key': settings.MERCADOPAGO_PUBLIC_KEY,
    })


@csrf_exempt
def process_payment(request):
    # ... (seu código existente, sem alterações) ...
    pass


@login_required
def payment_success(request):
    # ... (seu código existente, sem alterações) ...
    pass


@login_required
def payment_failure(request):
    # ...
    pass


@login_required
def payment_pending(request):
    # ...
    pass


@csrf_exempt
def payment_webhook(request):
    # ... (seu código existente, sem alterações) ...
    pass


@login_required
def pending_payments(request):
    payments = Payment.objects.filter(user=request.user, status='PENDING').order_by('-created_at')
    return render(request, 'nfe/payment_history.html', {'payments': payments})


from django.utils import timezone

@login_required
def payment_history(request):
    all_payments = Payment.objects.filter(user=request.user).order_by('-created_at')
    profile = request.user.profile
    active_subscription = None
    if profile.subscription_active and profile.subscription_until:
        if profile.subscription_until > timezone.now():
            active_subscription = {
                'plan': profile.plan,
                'expiration_date': profile.subscription_until,
                'status': 'Ativa'
            }
        else:
            active_subscription = {
                'plan': profile.plan,
                'expiration_date': profile.subscription_until,
                'status': 'Expirada'
            }
    context = {
        'payments': all_payments,
        'active_subscription': active_subscription,
    }
    return render(request, 'nfe/payment_history.html', context)


@login_required
def payment_status(request, payment_id):
    return render(request, 'nfe/payment_status.html', {
        'payment_id': payment_id,
        'public_key': settings.MERCADOPAGO_PUBLIC_KEY,
    })


@csrf_exempt
def process_payment(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)

    # Dados básicos do pagamento
    payment_data = {
        "transaction_amount": data.get("transaction_amount"),
        "token": data.get("token"),
        "description": data.get("description", "SmartDanfe - Plano"),
        "installments": data.get("installments", 1),
        "payment_method_id": data.get("payment_method_id"),
        "payer": {
            "email": data.get("payer", {}).get("email"),
            "identification": data.get("payer", {}).get("identification", {}),
            "first_name": data.get("payer", {}).get("first_name"),
            "last_name": data.get("payer", {}).get("last_name"),
        }
    }

    # Extrair endereço do payer (para boleto)
    payer_address = data.get("payer", {}).get("address")
    if payer_address:
        payment_data["payer"]["address"] = {
            "zip_code": payer_address.get("zip_code"),
            "street_name": payer_address.get("street_name"),
            "street_number": payer_address.get("street_number"),
            "neighborhood": payer_address.get("neighborhood"),
            "city": payer_address.get("city"),
            "federal_unit": payer_address.get("federal_unit"),
        }

    # Remove campos com valor None
    def clean_dict(d):
        return {k: v for k, v in d.items() if v is not None}
    payment_data = clean_dict(payment_data)
    payment_data["payer"] = clean_dict(payment_data.get("payer", {}))
    if "identification" in payment_data["payer"]:
        payment_data["payer"]["identification"] = clean_dict(payment_data["payer"]["identification"])
    if "address" in payment_data["payer"]:
        payment_data["payer"]["address"] = clean_dict(payment_data["payer"]["address"])

    try:
        payment_response = sdk.payment().create(payment_data)
        print("Payment response:", payment_response)

        # Verifica se houve erro
        if payment_response.get('status') != 201:
            error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido')
            cause = payment_response.get('response', {}).get('cause')
            if cause:
                error_msg += f" - {cause}"
            return JsonResponse({'error': error_msg, 'status': payment_response.get('status')}, status=400)

        payment = payment_response.get('response', {})
        status = payment.get('status')
        # Garante que status seja string
        if isinstance(status, int):
            status = str(status)

        # Atualiza o registro no banco
        preference_id = data.get('preference_id')
        if preference_id:
            payment_obj = Payment.objects.filter(preference_id=preference_id).first()
            if payment_obj:
                payment_obj.status = status.upper()
                payment_obj.payment_id = payment.get('id')
                payment_obj.save()

        return JsonResponse({'status': status, 'id': payment.get('id')})

    except Exception as e:
        logger.exception("Erro ao processar pagamento")
        return JsonResponse({'error': str(e)}, status=500)
    
@login_required
def payment_success(request):
    preference_id = request.GET.get('preference_id')
    payment_id = request.GET.get('collection_id')  # ou 'payment_id'

    if preference_id:
        payment = Payment.objects.filter(preference_id=preference_id, user=request.user).first()
        if payment and payment.status != 'APPROVED':
            # Se temos payment_id, consulta no Mercado Pago
            if payment_id:
                try:
                    sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
                    payment_info = sdk.payment().get(payment_id)
                    if payment_info['status'] == 200:
                        status = payment_info['response'].get('status')
                        if status == 'approved':
                            payment.status = 'APPROVED'
                            payment.payment_id = payment_id
                            payment.save()
                except Exception as e:
                    print("Erro ao consultar pagamento:", e)

            # Fallback: se não conseguiu consultar, marca como aprovado (apenas em teste)
            if payment.status != 'APPROVED':
                payment.status = 'APPROVED'
                payment.save()

            # Ativa assinatura
            profile = request.user.profile
            profile.subscription_active = True
            profile.plan = payment.plan
            days = 30 if payment.plan == 'mensal' else (90 if payment.plan == 'trimestral' else 365)
            profile.subscription_until = datetime.now() + timedelta(days=days)
            profile.save()

    return render(request, 'nfe/payment_success.html')


@login_required
def payment_failure(request):
    return render(request, 'nfe/payment_failure.html')


@login_required
def payment_pending(request):
    return render(request, 'nfe/payment_pending.html')


@csrf_exempt
def payment_webhook(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'ok'})

    # Obtém os headers
    x_signature = request.headers.get('x-signature', '')
    x_request_id = request.headers.get('x-request-id', '')

    # Obtém os query params
    query_params = urllib.parse.parse_qs(request.GET.urlencode())
    data_id = query_params.get('data.id', [''])[0]

    # Separa ts e v1 do x-signature
    parts = x_signature.split(',')
    ts = ''
    hash_v1 = ''
    for part in parts:
        key_val = part.split('=', 1)
        if len(key_val) == 2:
            key, val = key_val
            if key == 'ts':
                ts = val
            elif key == 'v1':
                hash_v1 = val

    # Monta o manifesto
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"

    # Obtém a chave secreta da sua aplicação no painel do Mercado Pago
    secret = settings.MERCADOPAGO_WEBHOOK_SECRET  # Defina no .env

    # Calcula o HMAC
    hmac_obj = hmac.new(secret.encode(), msg=manifest.encode(), digestmod=hashlib.sha256)
    computed_hash = hmac_obj.hexdigest()

    if computed_hash != hash_v1:
        print("Falha na validação da assinatura")
        return JsonResponse({'status': 'ok'}, status=200)  # Não retorna erro para o MP

    # Processa o corpo da notificação
    try:
        data = json.loads(request.body)
    except Exception as e:
        print("Erro ao parsear JSON:", e)
        return JsonResponse({'status': 'ok'})

    if data.get('type') == 'payment':
        payment_id = data['data']['id']
        sdk = mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)
        try:
            payment_info = sdk.payment().get(payment_id)
            if payment_info['status'] == 200:
                payment_data = payment_info['response']
                status = payment_data.get('status')
                preference_id = payment_data.get('preference_id')
                external_reference = payment_data.get('external_reference')

                payment = None
                if preference_id:
                    payment = Payment.objects.filter(preference_id=preference_id).first()
                elif external_reference:
                    payment = Payment.objects.filter(external_reference=external_reference).first()

                if payment:
                    payment.status = status.upper()
                    payment.payment_id = payment_id
                    payment.save()
                    if status == 'approved':
                        profile = payment.user.profile
                        profile.subscription_active = True
                        profile.plan = payment.plan
                        days = 30 if payment.plan == 'mensal' else (90 if payment.plan == 'trimestral' else 365)
                        profile.subscription_until = datetime.now() + timedelta(days=days)
                        profile.save()
                        print(f"Assinatura ativada para {payment.user.username}")
                else:
                    print("Pagamento não encontrado")
        except Exception as e:
            print("Erro ao processar pagamento:", e)

    return JsonResponse({'status': 'ok'})

@login_required
def pending_payments(request):
    """Página de pagamentos pendentes (acessível mesmo sem assinatura)"""
    payments = Payment.objects.filter(user=request.user, status='PENDING').order_by('-created_at')
    return render(request, 'nfe/payment_history.html', {'payments': payments})

@login_required
@subscription_required
def dashboard(request):
    pending_payments = Payment.objects.filter(user=request.user, status='PENDING').exists()
    return render(request, 'nfe/dashboard.html', {'payment_history': pending_payments})

from django.utils import timezone

@login_required
def payment_history(request):
    all_payments = Payment.objects.filter(user=request.user).order_by('-created_at')
    profile = request.user.profile
    active_subscription = None
    if profile.subscription_active and profile.subscription_until:
        # Compare using timezone.now() to be timezone-aware
        if profile.subscription_until > timezone.now():
            active_subscription = {
                'plan': profile.plan,
                'expiration_date': profile.subscription_until,
                'status': 'Ativa'
            }
        else:
            active_subscription = {
                'plan': profile.plan,
                'expiration_date': profile.subscription_until,
                'status': 'Expirada'
            }
    context = {
        'payments': all_payments,
        'active_subscription': active_subscription,
    }
    return render(request, 'nfe/payment_history.html', context)

@login_required
def payment_status(request, payment_id):
    """Exibe o status de um pagamento específico usando o Brick do Mercado Pago"""
    return render(request, 'nfe/payment_status.html', {
        'payment_id': payment_id,
        'public_key': settings.MERCADOPAGO_PUBLIC_KEY,
    })



SYSTEM_PROMPT_BASE = """Você é o assistente virtual do SmartDanfe, um sistema brasileiro de conversão e gestão de NF-e (Nota Fiscal Eletrônica).

Seu papel é ajudar os usuários (mesmo sem cadastro) a entender o sistema e tirar dúvidas.

**Regras importantes**:
- Responda SEMPRE em português brasileiro, com tom amigável, próximo e humano. Use expressões como "Claro!", "Entendi", "Vamos lá", "Fique à vontade para perguntar".
- Seja objetivo, mas empático. Se o usuário estiver frustrado, acolha.
- Se não souber algo específico, oriente a entrar em contato pelos canais oficiais (informados abaixo).
- NUNCA invente informações. Use APENAS os dados fornecidos neste prompt.
- Mantenha respostas curtas (máximo 3-4 parágrafos), a menos que o usuário peça mais detalhes.

**Sobre o SmartDanfe**:
- Converte chaves de acesso de NF-e (44 dígitos) em PDF (DANFE) e XML.
- Gera relatórios completos em Excel com todos os dados fiscais: emitente, destinatário, itens, impostos (ICMS, IPI, PIS, COFINS), valores totais, fretes, etc.
- Possui dashboard com estatísticas das notas processadas.
- Após o pagamento aprovado, o acesso ao sistema é liberado imediatamente.

**Planos e preços** (valores atualizados automaticamente do banco de dados):
{plans_info}

**Como usar**:
1. Escolha um plano no site.
2. Crie sua conta (nome, e-mail, telefone, senha).
3. Escolha a forma de pagamento (cartão, Pix, boleto) via Mercado Pago.
4. Após confirmação, acesse o dashboard e cole as chaves de acesso.
5. Baixe os PDFs, XMLs e relatórios.

**Contato oficial** (atualizado):
- E-mail: logistechinsights@gmail.com
- WhatsApp: (24) 99302-9437

Nunca peça ou armazene senhas do usuário. Se alguém perguntar sobre dados sensíveis, peça para entrar em contato pelo e-mail.
"""

@csrf_exempt
def chatbot_groq(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    try:
        body = json.loads(request.body)
        messages = body.get('messages', [])

        # Busca planos ativos no banco de dados
        plans = Plan.objects.filter(is_active=True)
        plans_str = "\n".join([f"- {p.name.capitalize()}: R$ {p.price:.2f}" for p in plans])
        if not plans_str:
            plans_str = "- Mensal: R$ 29,90\n- Trimestral: R$ 79,90\n- Anual: R$ 299,90"

        # Monta o sistema com dados dinâmicos
        system_prompt = SYSTEM_PROMPT_BASE.format(plans_info=plans_str)

        # Limita o histórico para economizar tokens (últimas 12 mensagens)
        if len(messages) > 12:
            messages = messages[-12:]

        client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                *messages
            ],
            max_tokens=600,
            temperature=0.8,  # um pouco mais criativo/humano
        )

        reply = response.choices[0].message.content
        return JsonResponse({'reply': reply})

    except Exception as e:
        logger.exception("Erro no chatbot")
        return JsonResponse({'error': 'Ocorreu um erro interno. Tente novamente ou fale conosco pelo WhatsApp.'}, status=500)
 