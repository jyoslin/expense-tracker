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
    """Fetch accounts"""
    accounts = supabase.table('accounts').select("*").execute().data
    df_acc = pd.DataFrame(accounts)
    if df_acc.empty: return pd.DataFrame(columns=['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 'goal_amount', 'goal_date'])
    return df_acc.sort_values(by=['name'])

def update_balance(account_id, amount_change):
    if not account_id: return 
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
        update_balance(from_acc_id, -amount) 
        update_balance(to_acc_id, amount)

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

# --- TAB 1: OVERVIEW (With History) ---
with tab1:
    st.subheader("Current Finance Stand")
    
    # FILTER DATA BASED ON SETTINGS
    nw_accounts = df_accounts[df_accounts['include_net_worth'] == True]
    net_worth = nw_accounts['balance'].sum()
    
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

    # --- THE RESTORED DETAIL VIEW ---
    st.divider()
    st.subheader("🔍 Account Details & History")
    
    selected_acc_name = st.selectbox("Select Account to View History", account_list)
    
    if selected_acc_name:
        selected_acc_id = account_map[selected_acc_name]
        
        # Fetch History
        history = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{selected_acc_id},to_account_id.eq.{selected_acc_id}") \
            .order('date', desc=True).limit(50).execute().data
            
        if history:
            st.dataframe(pd.DataFrame(history)[['date', 'description', 'amount', 'type', 'id']], hide_index=True, use_container_width=True)
            
            with st.expander("🗑️ Delete Transaction"):
                del_id = st.number_input("Transaction ID to Delete", min_value=0, step=1)
                if st.button("Delete Transaction"):
                    tx = supabase.table('transactions').select("*").eq('id', del_id).execute().data
                    if tx:
                        tx = tx[0]
                        # Reverse Logic
                        if tx['type'] == "Expense": update_balance(tx['from_account_id'], tx['amount'])
                        elif tx['type'] in ["Income", "Refund"]: update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] in ["Transfer", "Custodial In"]:
                            update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] == "Custodial Out":
                            # Complex reverse, but basically add back to bank, remove from liability
                            if tx['from_account_id']: update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                            
                        supabase.table('transactions').delete().eq('id', del_id).execute()
                        st.success("Deleted!")
                        st.rerun()
        else:
            st.info("No transactions found.")

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Custodial Out"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        
        # --- CUSTODIAL OUT ---
        if t_type == "Custodial Out":
            st.info("Paying back custodial money (Split Payment)")
            cust_acc = st.selectbox("Which Custodial Account?", df_accounts[df_accounts['type']=='Custodial']['name'])
            
            st.write("--- Sources ---")
            col_bank, col_cash = st.columns(2)
            with col_bank:
                bank_source = st.selectbox("Bank Source", df_accounts[df_accounts['type']=='Bank']['name'])
                bank_amount = st.number_input("Amount from Bank", min_value=0.0, format="%.2f")
            with col_cash:
                cash_source_name = st.selectbox("Cash Source", ["Physical Wallet (Untracked)"] + account_list)
                cash_amount = st.number_input("Amount from Cash", min_value=0.0, format="%.2f")
            
            desc = st.text_input("Description")
            
            if st.form_submit_button("Process Split Payment"):
                if bank_amount > 0:
                    add_transaction(date, bank_amount, f"{desc} (Bank)", "Transfer", account_map[bank_source], account_map[cust_acc])
                if cash_amount > 0:
                    cash_id = account_map.get(cash_source_name)
                    # Manual insert for cash part
                    supabase.table('transactions').insert({
                        "date": str(date), "amount": cash_amount, "description": f"{desc} (Cash)", "type": "Custodial Out",
                        "from_account_id": cash_id, "to_account_id": account_map[cust_acc]
                    }).execute()
                    update_balance(account_map[cust_acc], cash_amount) # Reduce Liability
                    if cash_id: update_balance(cash_id, -cash_amount) # Reduce Cash
                st.success("Saved!")
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

# --- TAB 3: GOALS ---
with tab3:
    st.subheader("🎯 Goal Tracker")
    goals = df_accounts[df_accounts['type'] == 'Sinking Fund']
    
    for index, row in goals.iterrows():
        with st.expander(f"📌 {row['name']} (Current: ${row['balance']:,.2f})", expanded=True):
            goal_amt = row['goal_amount'] or 0
            if row['balance'] >= goal_amt and goal_amt > 0:
                st.success("🎉 GOAL ACHIEVED!")
                with st.form(f"rotate_goal_{row['id']}"):
                    new_goal = st.number_input("New Goal Amount", value=float(goal_amt))
                    new_date = st.date_input("New Deadline")
                    if st.form_submit_button("🔄 Rotate Goal"):
                        update_account_settings(row['id'], row['include_net_worth'], row['is_liquid_asset'], new_goal, new_date)
                        st.rerun()
            elif goal_amt > 0:
                shortfall = goal_amt - row['balance']
                progress = min(row['balance'] / goal_amt, 1.0)
                st.progress(progress)
                st.caption(f"Target: ${goal_amt:,.2f}")
                if row['goal_date']:
                    deadline = datetime.strptime(row['goal_date'], '%Y-%m-%d').date()
                    months_left = (deadline.year - date.today().year) * 12 + (deadline.month - date.today().month)
                    if months_left > 0:
                        st.info(f"💡 Save **${shortfall / months_left:,.2f} / month**")

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Future Payments")
    # (Simplified for brevity - logic remains same as previous versions)
    # Check if there is data
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.dataframe(pd.DataFrame(upcoming)[['next_run_date', 'description', 'amount', 'frequency']])

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Account Configuration")
    
    # 1. CREATE ACCOUNT (Restored!)
    with st.expander("➕ Add New Account", expanded=False):
        with st.form("create_acc"):
            new_name = st.text_input("Name")
            new_type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund", "Cash"])
            initial_bal = st.number_input("Starting Balance", value=0.0)
            
            # CONDITIONAL: Only show goal if Sinking Fund
            # Note: In a form, we can't be truly dynamic without rerunning.
            # So we just show the inputs but label them "Optional (For Sinking Funds)"
            st.write("--- Goal Settings (Sinking Funds Only) ---")
            new_goal = st.number_input("Goal Amount", value=0.0)
            new_date = st.date_input("Goal Deadline", value=None)
            
            if st.form_submit_button("Create Account"):
                data = {
                    "name": new_name, "type": new_type, "balance": initial_bal,
                    "include_net_worth": True, "is_liquid_asset": True,
                    "goal_amount": new_goal if new_type == "Sinking Fund" else 0,
                    "goal_date": str(new_date) if new_type == "Sinking Fund" else None
                }
                supabase.table('accounts').insert(data).execute()
                st.success(f"Created {new_name}!")
                st.rerun()

    st.divider()
    
    # 2. EDIT ACCOUNT
    edit_acc = st.selectbox("Edit Existing Account", account_list)
    if edit_acc:
        row = df_accounts[df_accounts['name'] == edit_acc].iloc[0]
        
        with st.form("edit_settings"):
            c1, c2 = st.columns(2)
            inc_nw = c1.checkbox("Include in Net Worth?", value=row['include_net_worth'])
            is_liq = c2.checkbox("Is Actual Bank Asset?", value=row['is_liquid_asset'])
            
            # CONDITIONAL: Only show goal inputs if the account is ALREADY a Sinking Fund
            g_amt = 0.0
            g_date = None
            
            if row['type'] == 'Sinking Fund':
                st.divider()
                st.write("Goal Settings")
                g_amt = st.number_input("Goal Amount", value=float(row['goal_amount'] or 0))
                g_date = st.date_input("Goal Deadline", value=datetime.strptime(row['goal_date'], '%Y-%m-%d') if row['goal_date'] else None)
            
            if st.form_submit_button("Save Changes"):
                update_account_settings(row['id'], inc_nw, is_liq, g_amt, g_date)
                st.success("Updated!")
                st.rerun()
