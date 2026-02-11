import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

# --- 1. SETUP ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

# --- 2. HELPER FUNCTIONS ---
def get_accounts():
    """Fetch accounts with new Goal and Flag columns"""
    accounts = supabase.table('accounts').select("*").execute().data
    df_acc = pd.DataFrame(accounts)
    if df_acc.empty: return pd.DataFrame(columns=['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 'goal_amount', 'goal_date'])
    return df_acc.sort_values(by=['name'])

def update_balance(account_id, amount_change):
    if not account_id: return # Skip if no account selected (e.g. untracked cash)
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id):
    # Record
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id
    }).execute()

    # Update Balances
    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: update_balance(to_acc_id, amount)
    elif type == "Transfer":
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)
    elif type == "Custodial In": 
        # Liability Increases (More Negative), Bank Increases
        update_balance(from_acc_id, -amount) 
        update_balance(to_acc_id, amount)
    elif type == "Custodial Out":
        # Liability Decreases (Less Negative), Bank/Cash Decreases
        # Note: We handle the logic in the UI button to split multiple sources
        pass 

def update_account_settings(id, include_nw, is_asset, goal_amt, goal_date):
    """Save account preferences"""
    data = {
        "include_net_worth": include_nw,
        "is_liquid_asset": is_asset,
        "goal_amount": goal_amt,
        "goal_date": str(goal_date) if goal_date else None
    }
    supabase.table('accounts').update(data).eq("id", id).execute()

# --- 3. APP INTERFACE ---
st.title("💰 My Wealth Manager")

df_accounts = get_accounts()
account_map = dict(zip(df_accounts['name'], df_accounts['id']))
account_list = df_accounts['name'].tolist()

# TABS
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW (The "Finance Stand") ---
with tab1:
    st.subheader("Current Finance Stand")
    
    # FILTER DATA BASED ON SETTINGS
    # 1. Net Worth: Only accounts where 'include_net_worth' is TRUE
    nw_accounts = df_accounts[df_accounts['include_net_worth'] == True]
    net_worth = nw_accounts['balance'].sum()
    
    # 2. Liquid Assets: Only accounts where 'is_liquid_asset' is TRUE (e.g., Banks, Cash)
    asset_accounts = df_accounts[df_accounts['is_liquid_asset'] == True]
    total_liquid = asset_accounts['balance'].sum()
    
    c1, c2 = st.columns(2)
    c1.metric("Net Worth (Configured)", f"${net_worth:,.2f}") 
    c2.metric("Liquid Bank Assets", f"${total_liquid:,.2f}")

    st.divider()
    st.write("### Account Breakdown")
    # Show status icons
    df_display = df_accounts.copy()
    df_display['Net Worth?'] = df_display['include_net_worth'].apply(lambda x: "✅" if x else "❌")
    st.dataframe(df_display[['name', 'balance', 'type', 'Net Worth?']], hide_index=True, use_container_width=True)

# --- TAB 2: ENTRY (Now with Split Custodial) ---
with tab2:
    st.subheader("New Transaction")
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Custodial Out"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        
        # --- CUSTODIAL OUT (The Complex Logic) ---
        if t_type == "Custodial Out":
            st.info("Paying back custodial money (Split Payment)")
            cust_acc = st.selectbox("Which Custodial Account are you clearing?", 
                                    df_accounts[df_accounts['type']=='Custodial']['name'])
            
            st.write("--- Sources ---")
            col_bank, col_cash = st.columns(2)
            
            with col_bank:
                bank_source = st.selectbox("Bank Source", df_accounts[df_accounts['type']=='Bank']['name'])
                bank_amount = st.number_input("Amount from Bank", min_value=0.0, format="%.2f")
            
            with col_cash:
                # Optional: If you track a physical cash account, select it. If not, we just record it.
                cash_source_name = st.selectbox("Cash Source", ["Physical Wallet (Untracked)"] + account_list)
                cash_amount = st.number_input("Amount from Cash", min_value=0.0, format="%.2f")
            
            total_out = bank_amount + cash_amount
            st.metric("Total Being Paid Out", f"${total_out:,.2f}")
            
            desc = st.text_input("Description (e.g. Returned to John)")
            
            if st.form_submit_button("Process Split Payment"):
                # 1. Bank Portion
                if bank_amount > 0:
                    add_transaction(date, bank_amount, f"{desc} (Bank Part)", "Transfer", 
                                    account_map[bank_source], account_map[cust_acc])
                
                # 2. Cash Portion
                if cash_amount > 0:
                    cash_id = account_map.get(cash_source_name) # Will be None if "Untracked"
                    # If untracked, we just want to reduce the Custodial Liability.
                    # We treat it as a "Transfer" from Null -> Custodial
                    # But add_transaction handles balance updates. 
                    # If cash_id is None, 'update_balance' simply skips the source deduction (which is what we want for untracked cash)
                    # But we MUST update the destination (Custodial) to reduce liability.
                    
                    # We manually insert/update for this edge case to be safe
                    supabase.table('transactions').insert({
                        "date": str(date), "amount": cash_amount, "description": f"{desc} (Cash Part)", "type": "Custodial Out",
                        "from_account_id": cash_id, "to_account_id": account_map[cust_acc]
                    }).execute()
                    
                    # Reduce Liability (Custodial is negative, so we ADD to it to make it closer to 0)
                    update_balance(account_map[cust_acc], cash_amount)
                    
                    # Reduce Cash Account (if it exists)
                    if cash_id:
                        update_balance(cash_id, -cash_amount)

                st.success("Split Payment Recorded!")
                st.rerun()

        # --- STANDARD TRANSACTIONS ---
        else:
            amt = c2.number_input("Amount", min_value=0.01)
            f_acc, t_acc = None, None
            
            if t_type == "Expense": f_acc = st.selectbox("Paid From", account_list)
            elif t_type in ["Income", "Refund"]: t_acc = st.selectbox("Deposit To", account_list)
            elif t_type == "Transfer":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("From", account_list)
                t_acc = c_b.selectbox("To", account_list)
            elif t_type == "Custodial In":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("Custodial Source", df_accounts[df_accounts['type']=='Custodial']['name'])
                t_acc = c_b.selectbox("Bank Received", df_accounts[df_accounts['type']=='Bank']['name'])

            desc = st.text_input("Description")
            
            if st.form_submit_button("Submit"):
                add_transaction(date, amt, desc, t_type, account_map.get(f_acc), account_map.get(t_acc))
                st.success("Saved!")
                st.rerun()

# --- TAB 3: SINKING FUNDS (Rotating Goals) ---
with tab3:
    st.subheader("🎯 Goal Tracker")
    
    # Filter only Sinking Funds
    goals = df_accounts[df_accounts['type'] == 'Sinking Fund']
    
    for index, row in goals.iterrows():
        with st.expander(f"📌 {row['name']} (Current: ${row['balance']:,.2f})", expanded=True):
            
            # 1. CHECK IF GOAL MET
            goal_amt = row['goal_amount'] or 0
            if row['balance'] >= goal_amt and goal_amt > 0:
                st.success(f"🎉 GOAL ACHIEVED! Target was ${goal_amt:,.2f}")
                st.write("### Ready for the next target?")
                
                with st.form(f"rotate_goal_{row['id']}"):
                    new_goal = st.number_input("New Goal Amount", value=float(goal_amt))
                    new_date = st.date_input("New Deadline")
                    if st.form_submit_button("🔄 Rotate Goal (Set New Target)"):
                        update_account_settings(row['id'], row['include_net_worth'], row['is_liquid_asset'], new_goal, new_date)
                        st.rerun()
            
            # 2. SHOW PROGRESS
            elif goal_amt > 0:
                shortfall = goal_amt - row['balance']
                progress = min(row['balance'] / goal_amt, 1.0)
                st.progress(progress)
                st.caption(f"Progress: ${row['balance']:,.2f} / ${goal_amt:,.2f}")
                
                # 3. CALCULATE MONTHLY NEED
                if row['goal_date']:
                    deadline = datetime.strptime(row['goal_date'], '%Y-%m-%d').date()
                    today = date.today()
                    
                    # Calculate months remaining
                    months_left = (deadline.year - today.year) * 12 + (deadline.month - today.month)
                    
                    if months_left <= 0:
                        st.error("Deadline Passed!")
                    else:
                        monthly_need = shortfall / months_left
                        st.info(f"💡 You need to save **${monthly_need:,.2f} / month** to reach this by {row['goal_date']}")
            else:
                st.warning("No goal set for this fund.")

# --- TAB 5: SETTINGS (Now with Switches) ---
with tab5:
    st.subheader("🔧 Account Configuration")
    
    # Edit Existing Accounts
    edit_acc = st.selectbox("Edit Account", account_list)
    if edit_acc:
        row = df_accounts[df_accounts['name'] == edit_acc].iloc[0]
        
        with st.form("edit_settings"):
            c1, c2 = st.columns(2)
            inc_nw = c1.checkbox("Include in Net Worth?", value=row['include_net_worth'])
            is_liq = c2.checkbox("Is Actual Bank Asset?", value=row['is_liquid_asset'])
            
            st.divider()
            st.write("Goal Settings (For Sinking Funds)")
            g_amt = st.number_input("Goal Amount", value=float(row['goal_amount'] or 0))
            g_date = st.date_input("Goal Deadline", value=datetime.strptime(row['goal_date'], '%Y-%m-%d') if row['goal_date'] else None)
            
            if st.form_submit_button("Save Settings"):
                update_account_settings(row['id'], inc_nw, is_liq, g_amt, g_date)
                st.success("Updated!")
                st.rerun()
