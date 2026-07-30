import sys
sys.stdout.reconfigure(encoding='utf-8')
import time
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from datetime import datetime
import json
import os
from solana.rpc.commitment import Confirmed
import requests
import io
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---


# --- Envoie Discord ---
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
NOM_FICHIER_JSON = "./resultats_abdoul.json"
FICHIER_ETAT = "webhook_state.json"

# Remplace par ton URL RPC privée (Helius/Quicknode) sinon ça sera trop lent !
RPC_URL = os.environ["HELIUS_RPC_URL"]

# Les 2 wallets cibles à surveiller
BINANCE_WALLETS_STR = [
    "2dtToe6KUwWNrX9qnMvmDfo56iMGNSUo8icBH83RTaCz",
    "CP61dR6YsX3sirPg6XgGTJEV3pcD4HMLFdz8Kuk8Xt7K",
]
BINANCE_WALLETS = [Pubkey.from_string(addr) for addr in BINANCE_WALLETS_STR]

# Nom à attribuer au wallet détecté selon le wallet cible qui a reçu les SOL
NOMS_WALLETS_CIBLES = {
    "2dtToe6KUwWNrX9qnMvmDfo56iMGNSUo8icBH83RTaCz": "Bybit_Insider_Absoul",
    "CP61dR6YsX3sirPg6XgGTJEV3pcD4HMLFdz8Kuk8Xt7K": "Gate_Insider_Absoul",
}


# OPTIMISATION 1 : On utilise 'Confirmed' (suffisant et rapide)
client = Client(RPC_URL, commitment=Confirmed)

# --- MEMOIRE DU BOT ---
# Ce dictionnaire servira à retenir la dernière transaction vue pour CHAQUE wallet
# Structure : { "adresse_binance_1": "signature_xyz", "adresse_binance_2": "signature_abc" }
dernieres_signatures = {str(wallet): None for wallet in BINANCE_WALLETS}


def envoyer_fichier_discord(fichier_source, webhook_url, fichier_etat):
    
    # 1. Suppression de l'ancien message (inchangé)
    if os.path.exists(fichier_etat):
        with open(fichier_etat, 'r') as f:
            try:
                etat = json.load(f)
                ancien_id = etat.get('message_id')
                if ancien_id:
                    requests.delete(f"{webhook_url}/messages/{ancien_id}")
            except:
                pass

    # 2. Préparation avec la "Signature Magique" (BOM)
    try:
        # A. On charge les données proprement
        with open(fichier_source, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # B. On remet les vrais emojis (ensure_ascii=False)
        json_str = json.dumps(data, indent=4, ensure_ascii=False)
        
        # C. L'ASTUCE ULTIME : On ajoute le BOM (b'\xef\xbb\xbf') au début
        # Cela force Discord à reconnaître l'UTF-8 immédiatement
        donnees_binaires = b'\xef\xbb\xbf' + json_str.encode('utf-8')
        
        fichier_en_memoire = io.BytesIO(donnees_binaires)

        # D. Envoi
        fichiers = {
            'file': (
                'resultats_abdoul.json',
                fichier_en_memoire,
                'application/json; charset=utf-8' 
            )
        }
        
        payload = {
            "content": f"**🔄 Mise à jour ({time.strftime('%H:%M:%S')}) :**\nVoir le fichier joint 👇"
        }
        
        response = requests.post(webhook_url + "?wait=true", data=payload, files=fichiers)

        # Si Discord nous rate-limit, on attend le temps indiqué et on retente une fois
        if response.status_code == 429:
            retry_after = response.json().get('retry_after', 1)
            print(f"⏳ Rate limit Discord, nouvelle tentative dans {retry_after}s...")
            time.sleep(retry_after)
            fichier_en_memoire.seek(0)
            response = requests.post(webhook_url + "?wait=true", data=payload, files=fichiers)

        if response.status_code in [200, 201, 204]:
            nouvel_id = response.json()['id']
            print(f"[{time.strftime('%H:%M:%S')}] Envoyé avec succès (ID: {nouvel_id}).")
            with open(fichier_etat, 'w') as f:
                json.dump({'message_id': nouvel_id}, f)
        else:
            print(f"Erreur envoi : {response.status_code} - {response.text}")

    except Exception as e:
        print(f"Erreur technique : {e}")
        



def charger_json():
    """Charge le contenu actuel du fichier JSON s'il existe."""
    if os.path.exists(NOM_FICHIER_JSON):
        try:
            with open(NOM_FICHIER_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def sauvegarder_dans_json(nouveau_wallet, nom_wallet):
    """Ajoute le nouveau wallet détecté au fichier JSON avec la structure demandée.
    Retourne True si un nouveau wallet a été ajouté, False si c'était déjà un doublon."""
    data = charger_json()

    # On vérifie si l'adresse n'est pas déjà dans la liste pour éviter les doublons
    deja_present = any(item['trackedWalletAddress'] == str(nouveau_wallet) for item in data)

    if not deja_present:
        nouvelle_entree = {
            "trackedWalletAddress": str(nouveau_wallet),
            "name": nom_wallet,
            "emoji": "💎",
            "createdAt": datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "alertsOnToast": True,
            "alertsOnBubble": True,
            "alertsOnFeed": True,
            "alertsOnTransfer": True,
            "toastOnTransfer": True,
            "groupNames": ["Abdoul"],
            "sound": "default",
            "transferAudio": ""
        }

        data.append(nouvelle_entree)

        with open(NOM_FICHIER_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"✅ Sauvegardé dans {NOM_FICHIER_JSON}")
        return True
    else:
        print(f"ℹ️ Ce wallet est déjà dans le fichier JSON.")
        return False
def analyser_transaction(signature, wallet_source):
    """
    Récupère les détails d'une transaction et identifie exactement
    l'expéditeur ou le destinataire qui a interagi avec le wallet cible.
    Retourne True si un NOUVEAU wallet a été ajouté au fichier JSON.
    """
    nouveau_wallet_ajoute = False
    try:
        tx_detail = client.get_transaction(signature, max_supported_transaction_version=0)

        if not tx_detail.value or not tx_detail.value.transaction.meta:
            return False

        timestamp = tx_detail.value.block_time
        if timestamp:
            date_lisible = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        else:
            date_lisible = "Inconnue"

        meta = tx_detail.value.transaction.meta
        pre_balances = meta.pre_balances
        post_balances = meta.post_balances
        account_keys = tx_detail.value.transaction.transaction.message.account_keys

        # 1. On stocke les différences de balance pour TOUS les comptes de la transaction
        changements = {}
        for i, key in enumerate(account_keys):
            diff_sol = (post_balances[i] - pre_balances[i]) / 1_000_000_000
            changements[str(key)] = {
                'diff': diff_sol,
                'key': key
            }

        wallet_cible_str = str(wallet_source)

        # Si le wallet cible n'est pas dans les comptes modifiés, on ignore
        if wallet_cible_str not in changements:
            return False

        diff_cible = changements[wallet_cible_str]['diff']

        # Tolérance pour ignorer le paiement des frais de réseau (gas fees)
        SEUIL_FRAIS = -0.005

        # --- LE WALLET CIBLE A REÇU DES SOL ---
        if diff_cible > 0:
            # On cherche le compte dont la balance a DIMINUÉ
            for addr, data in changements.items():
                if addr in BINANCE_WALLETS_STR:
                    continue # On s'ignore soi-même

                # Si ce compte a envoyé de l'argent
                if data['diff'] < SEUIL_FRAIS:
                    montant_envoye = abs(data['diff'])

                    print(f"🎯 BINGO ! RÉCEPTION DÉTECTÉE !")
                    print(f"Expéditeur : {addr}")
                    print(f"Date: {date_lisible}")
                    print(f"Montant: {montant_envoye:.5f} SOL")
                    print(f"Vers : Cible ({wallet_cible_str[:5]}...)")
                    print(f"Tx: https://solscan.io/tx/{signature}")

                    # On sauvegarde L'EXPÉDITEUR avec le nom associé au wallet cible
                    nom_wallet = NOMS_WALLETS_CIBLES.get(wallet_cible_str, "insider cashino")
                    if sauvegarder_dans_json(data['key'], nom_wallet):
                        nouveau_wallet_ajoute = True
                    print("-" * 30)

        return nouveau_wallet_ajoute

    except Exception as e:
        # print(f"Erreur d'analyse : {e}")
        return False
def main():
    print("🔭 Le Sniper est en marche...")

    while True:
        nouveaux_wallets_ce_cycle = False

        for wallet_actuel in BINANCE_WALLETS:
            try:
                adresse_str = str(wallet_actuel)
                last_sig = dernieres_signatures[adresse_str]
                # On demande un peu plus d'historique (limit=50) au cas où il y a eu
                # beaucoup de mouvements pendant la pause de 10s.
                # Cela ne coûte PAS plus cher si il n'y a rien de nouveau.
                resp = client.get_signatures_for_address(
                    wallet_actuel,
                    limit=50,
                    until=last_sig
                )

                transactions = resp.value

                if transactions:
                    # On met à jour le curseur pour la prochaine boucle
                    dernieres_signatures[adresse_str] = transactions[0].signature

                    # On parcourt les transactions de la plus ANCIENNE à la plus RÉCENTE
                    # (reversed) pour garder l'ordre chronologique logique
                    for tx in reversed(transactions):

                        # --- OPTIMISATION CRUCIALE ---
                        # Si la transaction a une erreur (failed), on l'ignore GRATUITEMENT
                        if tx.err is not None:
                            continue
                        # -----------------------------

                        if analyser_transaction(tx.signature, wallet_actuel):
                            nouveaux_wallets_ce_cycle = True

                        # Petite pause entre chaque appel RPC pour éviter de rate-limit Helius
                        # si beaucoup de transactions arrivent d'un coup
                        time.sleep(0.3)


            except Exception as e:
                print(f"⚠️ Erreur sur {str(wallet_actuel)[:5]}... : {e}")
                time.sleep(2)

        # Un seul envoi Discord par cycle, même si plusieurs wallets ont été détectés
        if nouveaux_wallets_ce_cycle:
            envoyer_fichier_discord(NOM_FICHIER_JSON, WEBHOOK_URL, FICHIER_ETAT)

        # OPTIMISATION 2 : On dort 30 secondes entre chaque cycle.
        # Grâce au paramètre `until=last_sig`, on récupérera tout ce qui s'est passé
        # pendant cette pause au prochain tour. Zéro perte de données.
        time.sleep(30)

if __name__ == "__main__":
    main()