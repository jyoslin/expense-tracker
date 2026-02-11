import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # For easy monthly math

# --- 1. SETUP ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

st.set_page_config(page_title="My Finance", page_icon="💰")

# --- 2. AUTOMATION ENGINE (The Robot) ---
def run_scheduled_transactions():
    """Checks for due payments and executes them"""
    today = datetime.today().date()
    
    # Fetch tasks due on or before today
    response = supabase.table('schedule').select("*").lte('next_run_date', str(today)).execute()
    tasks = response.data
    
    if tasks:
        count = 0
        for task in tasks:
            # 1. Execute the transaction
            add_transaction(
                date=task['next_run_date'],
                amount=task['amount'],
                description=f"🔄 {task['description']}", # Add icon to show it was auto
                type=task['type'],
                from_acc_id=task['from_account_id'],
                to_acc_id=task['to_account_id']
            )
            
            # 2. Update or Delete the schedule
            if task['frequency'] == 'Monthly':
                # Calculate next month date
                current_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date()
                next_date = current_date + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                # One-Time payment: Delete after running
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            
            count += 1
        return count
    return 0

# --- 3. HELPER FUNCTIONS ---
def get_accounts():
    """Fetch accounts sorted by usage frequency"""
    accounts = supabase.table('accounts').select("*").execute().data
    df_acc = pd.DataFrame(accounts)
    
    # Get Usage Counts
    trans_data = supabase.table('transactions').select("from_account_id, to_account_id").execute().data
    if not trans_data:
        return df_acc
        
    all_ids = [t['from_account_id'] for t in trans_data if t['from_account_id']] + \
              [t['to_account_id'] for t in trans_data if t['to_account_id']]
    freq = pd.Series(all_ids).value_counts()
    df_acc['usage'] = df_acc['id'].map(freq).fillna(0)
    
    return df_acc.sort_values(by=['usage', 'name'], ascending=[False, True])

def update_balance(account_id, amount_change):
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id):
    # Insert Transaction
    data = {
        "date": str(date),
        "amount": amount,
        "description": description,
        "type": type,
        "from_account_id": from_acc_id,
        "to_account_id": to_acc_id
    }
    supabase.table('transactions').insert(data).execute()

    # Update Balances
    if type == "Expense":
        update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: # Refund behaves like Income (Add money)
        update_balance(to_acc_id, amount)
    elif type == "Transfer":
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)

def add_schedule(description, amount, type, from_id, to_id, frequency, start_date):
    data = {
        "description": description,
        "amount": amount,
        "type": type,
        "from_account_id": from_id,
        "to_account_id": to_id,
        "frequency": frequency,
        "next_run_date": str(start_date)
    }
    supabase.table('schedule').insert(data).execute()

# --- 4. APP INTERFACE ---
st.title("💰 My Wealth Manager")

# Run the Robot (Check for due payments)
processed = run_scheduled_transactions()
if processed > 0:
    st.toast(f"✅ Processed {processed} scheduled transactions!", icon="🤖")

# Load Data
df_accounts = get_accounts()
account_map = dict(zip(df_accounts['name'], df_accounts['id']))
account_list = df_accounts['name'].tolist()

# TABS
tab1, tab2, tab3, tab4 = st.tabs(["📝 Entry", "📅 Future/Recurring", "📊 Dashboard", "⚙️ Settings"])

# --- TAB 1: ENTRY ---
with tab1:
    st.subheader("New Transaction")
    trans_type = st.radio("Type", ["Expense", "Income", "Transfer", "Refund"], horizontal=True)
    
    with st.form("entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            date = st.date_input("Date", datetime.today())
        with col2:
            amount = st.number_input("Amount ($)", min_value=0.01, format="%.2f")

        from_account, to_account = None, None
        
        if trans_type == "Expense":
            from_account = st.selectbox("Paid From", account_list)
        elif trans_type in ["Income", "Refund"]:
            # Refund: Money comes BACK to your account (e.g., Credit Card)
            to_account = st.selectbox("Deposit To (or Refund To)", account_list)
        elif trans_type == "Transfer":
            c1, c2 = st.columns(2)
            with c1: from_account = st.selectbox("From", account_list)
            with c2: to_account = st.selectbox("To", account_list)

        desc = st.text_input("Description")
        if st.form_submit_button("Submit"):
            f_id = account_map.get(from_account)
            t_id = account_map.get(to_account)
            add_transaction(date, amount, desc, trans_type, f_id, t_id)
            st.success("Saved!")
            st.rerun()

# --- TAB 2: SCHEDULER ---
with tab2:
    st.subheader("Set Up Future Payment")
    with st.form("schedule_form"):
        s_desc = st.text_input("Description (e.g. Netflix, Rent)")
        c1, c2, c3 = st.columns(3)
        with c1: s_amount = st.number_input("Amount", min_value=0.01)
        with c2: s_freq = st.selectbox("Frequency", ["Monthly", "One-Time"])
        with c3: s_date = st.date_input("Start Date", datetime.today())
        
        s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
        
        s_from, s_to = None, None
        if s_type == "Expense":
            s_from = st.selectbox("From Account", account_list)
        elif s_type == "Income":
            s_to = st.selectbox("To Account", account_list)
        elif s_type == "Transfer":
            s_from = st.selectbox("From", account_list, key="s_f")
            s_to = st.selectbox("To", account_list, key="s_t")
            
        if st.form_submit_button("Schedule It"):
            f_id = account_map.get(s_from)
            t_id = account_map.get(s_to)
            add_schedule(s_desc, s_amount, s_type, f_id, t_id, s_freq, s_date)
            st.success("Scheduled!")
            st.rerun()
            
    st.divider()
    st.write("### 🗓️ Upcoming Schedule")
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.dataframe(pd.DataFrame(upcoming)[['next_run_date', 'description', 'amount', 'frequency']])
        
        # Delete Schedule Button
        del_id = st.number_input("ID to Delete", min_value=0)
        if st.button("Delete Schedule"):
            supabase.table('schedule').delete().eq("id", del_id).execute()
            st.rerun()
    else:
        st.info("Nothing scheduled.")

# --- TAB 3: DASHBOARD ---
with tab3:
    st.subheader("🔍 Account Details")
    sel_acc = st.selectbox("Select Account", account_list)
    sel_id = account_map[sel_acc]
    
    # Balance
    curr = df_accounts[df_accounts['id'] == sel_id]['balance'].values[0]
    st.metric("Current Balance", f"${curr:,.2f}")
    
    # History
    st.divider()
    txs = supabase.table('transactions').select("*").or_(f"from_account_id.eq.{sel_id},to_account_id.eq.{sel_id}").order('date', desc=True).limit(20).execute().data
    if txs:
        st.dataframe(pd.DataFrame(txs)[['date', 'description', 'amount', 'type', 'id']], hide_index=True, use_container_width=True)
        
        # Delete Transaction Logic
        with st.expander("Delete a Transaction?"):
            tid = st.number_input("Transaction ID to Delete", min_value=0)
            if st.button("Delete Transaction"):
                # (Logic same as previous version - simple reverse)
                tx = supabase.table('transactions').select("*").eq('id', tid).execute().data[0]
                if tx['type'] == "Expense": update_balance(tx['from_account_id'], tx['amount'])
                elif tx['type'] in ["Income", "Refund"]: update_balance(tx['to_account_id'], -tx['amount'])
                elif tx['type'] == "Transfer":
                    update_balance(tx['from_account_id'], tx['amount'])
                    update_balance(tx['to_account_id'], -tx['amount'])
                supabase.table('transactions').delete().eq('id', tid).execute()
                st.success("Deleted!")
                st.rerun()

# --- TAB 4: SETTINGS ---
with tab4:
    st.write("Create Accounts")
    with st.form("new_acc"):
        n_name = st.text_input("Name")
        n_type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund"])
        if st.form_submit_button("Create"):
            supabase.table('accounts').insert({"name": n_name, "type": n_type, "balance": 0}).execute()
            st.success("Created!")
            st.rerun()
