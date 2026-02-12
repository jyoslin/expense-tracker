import streamlit as st
import hmac
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
import io

# --- 1. SECURITY & SETUP ---
st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        if hmac.compare_digest(st.session_state["password"], st.secrets["APP_PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input("🔒 Please enter your password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("😕 Password incorrect")
    return False

if not check_password():
    st.stop()

# Connect to Supabase
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- 2. CACHED HELPER FUNCTIONS ---

def clear_cache():
    st.cache_data.clear()

@st.cache_data(ttl=300)
def get_accounts(show_inactive=False):
    accounts = supabase.table('accounts').select("*").execute().data
    df = pd.DataFrame(accounts)
    
    # Define all columns we expect
    cols = ['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 
            'goal_amount', 'goal_date', 'sort_order', 'is_active', 'remark', 
            'currency', 'manual_exchange_rate']
            
    if df.empty: return pd.DataFrame(columns=cols)
    
    # Backfill missing columns for legacy data
    if 'sort_order' not in df.columns: df['sort_order'] = 99
    if 'is_active' not in df.columns: df['is_active'] = True
    if 'remark' not in df.columns: df['remark'] = ""
    if 'currency' not in df.columns: df['currency'] = "SGD"
    if 'manual_exchange_rate' not in df.columns: df['manual_exchange_rate'] = 1.0
    
    if not show_inactive:
        df = df[df['is_active'] == True]
        
    return df.sort_values(by=['sort_order', 'name'])

@st.cache_data(ttl=3600)
def get_categories(type_filter=None):
    query = supabase.table('categories').select("*")
    if type_filter:
        query = query.eq('type', type_filter)
    data = query.execute().data
    if not data: return []
    return sorted([item['name'] for item in data])

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def update_account_settings(id, name, balance, include_nw, is_asset, goal_amt, goal_date, sort_order, is_active, remark, currency, rate):
    data = {
        "name": name,
        "balance": balance,
        "include_net_worth": include_nw,
        "is_liquid_asset": is_asset,
        "goal_amount": goal_amt,
        "goal_date": str(goal_date) if goal_date else None,
        "sort_order": sort_order,
        "is_active": is_active,
        "remark": remark,
        "currency": currency,
        "manual_exchange_rate": rate
    }
    supabase.table('accounts').update(data).eq("id", id).execute()
    clear_cache()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id, category, remark):
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id, "category": category,
        "remark": remark
    }).execute()

    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: update_balance(to_acc_id, amount)
    elif type in ["Transfer", "Custodial In"]:
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)
    elif type == "Custodial Out":
        if from_acc_id: update_balance(from_acc_id, -amount) 
        update_balance(to_acc_id, amount)
    
    clear_cache()

def run_scheduled_transactions():
    today = datetime.today().date()
    tasks = supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', False).execute().data
    count = 0
    if tasks:
        for task in tasks:
            cat = task.get('category', 'Recurring')
            add_transaction(task['next_run_date'], task['amount'], f"🔄 {task['description']}", 
                          task['type'], task['from_account_id'], task['to_account_id'], cat, "Auto-Scheduled")
            
            if task['frequency'] == 'Monthly':
                next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            count += 1
    return count

def get_due_manual_tasks():
    today = datetime.today().date()
    return supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', True).execute().data

# --- 3. APP INTERFACE ---
st.title("💰 My Wealth Manager")

if 'scheduler_run' not in st.session_state:
    processed = run_scheduled_transactions()
    if processed: st.toast(f"Processed {processed} auto-payments!", icon="🤖")
    st.session_state['scheduler_run'] = True

df_active = get_accounts(show_inactive=False)
account_map = dict(zip(df_active['name'], df_active['id']))
account_list = df_active['name'].tolist() if not df_active.empty else []
non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []

manual_due = get_due_manual_tasks()
if manual_due:
    st.warning(f"🔔 You have {len(manual_due)} manual transfers due!")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW ---
with tab1:
    if manual_due:
        st.write("### 🔔 Pending Manual Transfers")
        for task in manual_due:
            with st.expander(f"Due: {task['next_run_date']} - {task['description']} (${task['amount']})", expanded=True):
                st.write(f"**Action:** {task['type']} of ${task['amount']}")
                if st.button("✅ I have done this transfer", key=f"done_{task['id']}"):
                    cat = task.get('category', 'Recurring')
                    add_transaction(date.today(), task['amount'], f"✅ {task['description']}", 
                                  task['type'], task['from_account_id'], task['to_account_id'], cat, "Manual Scheduled")
                    
                    if task['frequency'] == 'Monthly':
                        next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                        supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
                    else:
                        supabase.table('schedule').delete().eq("id", task['id']).execute()
                    st.success("Recorded!")
                    st.rerun()
        st.divider()

    st.subheader("Current Finance Stand (Base: SGD)")
    
    # CALCULATE NET WORTH WITH EXCHANGE RATES
    # Formula: Balance * Exchange Rate
    
    if not df_active.empty:
        df_calc = df_active.copy()
        df_calc['sgd_value'] = df_calc['balance'] * df_calc['manual_exchange_rate']
        
        nw_accounts = df_calc[df_calc['include_net_worth'] == True]
        net_worth = nw_accounts['sgd_value'].sum()
        
        asset_accounts = df_calc[df_calc['is_liquid_asset'] == True]
        total_liquid = asset_accounts['sgd_value'].sum()
    else:
        net_worth = 0
        total_liquid = 0
    
    c1, c2 = st.columns(2)
    c1.metric("Net Worth (Approx SGD)", f"${net_worth:,.2f}") 
    c2.metric("Liquid Assets (Approx SGD)", f"${total_liquid:,.2f}")

    st.divider()
    
    with st.expander("📂 View Account Breakdown (Original Currency)"):
        # Show currency in the table
        display_cols = ['name', 'balance', 'currency', 'type', 'remark']
        st.dataframe(df_active[display_cols], hide_index=True, use_container_width=True)

    st.divider()
    
    st.subheader("📊 Spending by Category")
    col_d1, col_d2 = st.columns(2)
    start_date = col_d1.date_input("Start Date", date.today().replace(day=1)) 
    end_date = col_d2.date_input("End Date", date.today())
    
    expenses = supabase.table('transactions').select("*") \
        .eq('type', 'Expense') \
        .gte('date', str(start_date)) \
        .lte('date', str(end_date)) \
        .order('date', desc=True).execute().data
        
    if expenses:
        df_exp = pd.DataFrame(expenses)
        if 'category' in df_exp.columns and not df_exp.empty:
            cat_sum = df_exp.groupby('category')['amount'].sum().reset_index().sort_values('amount', ascending=False)
            c_chart, c_list = st.columns([2, 1])
            with c_chart: st.bar_chart(cat_sum.set_index('category'))
            with c_list: st.dataframe(cat_sum, hide_index=True, use_container_width=True)
            
            st.write("---")
            st.write("**📂 Drill Down: Transaction Details**")
            sel_cat_view = st.selectbox("Select Category to View Details", cat_sum['category'].tolist())
            if sel_cat_view:
                df_detail = df_exp[df_exp['category'] == sel_cat_view]
                st.dataframe(df_detail[['date', 'description', 'remark', 'amount']], hide_index=True, use_container_width=True)
    else:
        st.info("No expenses found in this date range.")

    st.divider()
    
    st.subheader("🔍 Account Details & History")
    selected_acc_name = st.selectbox("Select Account", account_list)
    if selected_acc_name:
        selected_acc_id = account_map[selected_acc_name]
        history = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{selected_acc_id},to_account_id.eq.{selected_acc_id}") \
            .order('date', desc=True).limit(50).execute().data
            
        if history:
            # Show currency symbol for context
            acc_curr = df_active[df_active['id'] == selected_acc_id].iloc[0]['currency']
            st.write(f"**Currency: {acc_curr}**")
            st.dataframe(pd.DataFrame(history)[['date', 'category', 'description', 'remark', 'amount', 'type', 'id']], hide_index=True, use_container_width=True)
            
            with st.expander("🗑️ Delete Transaction"):
                del_id = st.number_input("Transaction ID to Delete", min_value=0, step=1)
                if st.button("Delete Transaction"):
                    tx = supabase.table('transactions').select("*").eq('id', del_id).execute().data
                    if tx:
                        tx = tx[0]
                        if tx['type'] == "Expense": update_balance(tx['from_account_id'], tx['amount'])
                        elif tx['type'] in ["Income", "Refund"]: update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] in ["Transfer", "Custodial In"]:
                            update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] == "Custodial Out":
                            if tx['from_account_id']: update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        supabase.table('transactions').delete().eq('id', del_id).execute()
                        clear_cache()
                        st.success("Deleted!")
                        st.rerun()

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Custodial Out"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        
        if t_type == "Custodial Out":
            st.info("Paying back custodial money (Split Payment)")
            cust_acc = st.selectbox("Which Custodial Account?", df_active[df_active['type']=='Custodial']['name']) if not df_active.empty else None
            
            st.write("--- Sources ---")
            col_bank, col_cash = st.columns(2)
            with col_bank:
                bank_source = st.selectbox("Bank Source", df_active[df_active['type'].isin(['Bank', 'Cash'])]['name'])
                bank_amount = st.number_input("Amount from Bank", min_value=0.0, format="%.2f")
            with col_cash:
                cash_source_name = st.selectbox("Cash Source", ["Physical Wallet (Untracked)"] + account_list)
                cash_amount = st.number_input("Amount from Cash", min_value=0.0, format="%.2f")
            
            desc = st.text_input("Description (Short)")
            remark = st.text_area("Remark / Notes (Optional)", height=68)
            category = "Custodial" 
            
            if st.form_submit_button("Process Split Payment"):
                if bank_amount > 0:
                    add_transaction(date, bank_amount, f"{desc} (Bank)", "Custodial Out", account_map[bank_source], account_map[cust_acc], category, remark)
                if cash_amount > 0:
                    cash_id = account_map.get(cash_source_name)
                    if not cash_id:
                        supabase.table('transactions').insert({
                            "date": str(date), "amount": cash_amount, "description": f"{desc} (Cash)", "type": "Custodial Out",
                            "to_account_id": account_map[cust_acc], "category": category, "remark": remark
                        }).execute()
                        update_balance(account_map[cust_acc], cash_amount)
                        clear_cache()
                    else:
                        add_transaction(date, cash_amount, f"{desc} (Cash)", "Custodial Out", cash_id, account_map[cust_acc], category, remark)
                st.success("Saved!")
                st.rerun()

        else:
            amt = c2.number_input("Amount", min_value=0.01)
            cat_options = get_categories("Income") if t_type == "Income" else get_categories("Expense")
            category = st.selectbox("Category", cat_options)
            
            f_acc, t_acc = None, None
            if t_type == "Expense": f_acc = st.selectbox("Paid From", non_loan_accounts)
            elif t_type in ["Income", "Refund"]: t_acc = st.selectbox("Deposit To", account_list)
            elif t_type == "Transfer":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("From", non_loan_accounts)
                t_acc = c_b.selectbox("To", account_list)
            elif t_type == "Custodial In":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("Custodial Source", df_active[df_active['type']=='Custodial']['name'])
                t_acc = c_b.selectbox("Bank Received", df_active[df_active['type']=='Bank']['name'])

            desc = st.text_input("Description (Short)")
            remark = st.text_area("Remark / Notes (Optional)", height=68)
            
            if st.form_submit_button("Submit"):
                add_transaction(date, amt, desc, t_type, account_map.get(f_acc), account_map.get(t_acc), category, remark)
                st.success("Saved!")
                st.rerun()

# --- TAB 3: GOALS ---
with tab3:
    st.subheader("🎯 Sinking Funds Dashboard")
    goals = df_active[df_active['type'] == 'Sinking Fund']
    
    if not goals.empty:
        # NOTE: Sinking Funds are summed raw for now (assuming usually same currency for saving goals)
        # Or convert to SGD if mixed. Let's convert to SGD for accurate progress.
        goals_calc = goals.copy()
        goals_calc['saved_sgd'] = goals_calc['balance'] * goals_calc['manual_exchange_rate']
        goals_calc['goal_sgd'] = goals_calc['goal_amount'] * goals_calc['manual_exchange_rate']
        
        total_saved = goals_calc['saved_sgd'].sum()
        total_goal = goals_calc['goal_sgd'].sum()
        
        if total_goal > 0: grand_progress = min(total_saved / total_goal, 1.0)
        else: grand_progress = 0.0
            
        st.write("### 🏆 Total Progress (SGD Equivalent)")
        c_gt1, c_gt2 = st.columns([1, 3])
        c_gt1.metric("Total Saved", f"${total_saved:,.2f}", f"Target: ${total_goal:,.2f}")
        c_gt2.write("")
        c_gt2.write("Overall Completion:")
        c_gt2.progress(grand_progress)
        
        st.divider()

        st.write("### Individual Funds (Original Currency)")
        for index, row in goals.iterrows():
            curr = row['currency']
            with st.expander(f"📌 {row['name']} (Current: {curr} {row['balance']:,.2f})", expanded=True):
                goal_amt = row['goal_amount'] or 0
                if row['balance'] >= goal_amt and goal_amt > 0:
                    st.success("🎉 GOAL ACHIEVED!")
                    with st.form(f"rotate_goal_{row['id']}"):
                        new_goal = st.number_input("New Goal Amount", value=float(goal_amt))
                        new_date = st.date_input("New Deadline")
                        if st.form_submit_button("🔄 Rotate Goal"):
                            update_account_settings(row['id'], row['name'], row['balance'], row['include_net_worth'], row['is_liquid_asset'], new_goal, new_date, row.get('sort_order', 99), row.get('is_active', True), row.get('remark', ''), row.get('currency', 'SGD'), row.get('manual_exchange_rate', 1.0))
                            st.rerun()
                elif goal_amt > 0:
                    shortfall = goal_amt - row['balance']
                    progress = min(row['balance'] / goal_amt, 1.0)
                    st.progress(progress)
                    st.caption(f"Target: {curr} {goal_amt:,.2f}")
                    if row['goal_date']:
                        deadline = datetime.strptime(row['goal_date'], '%Y-%m-%d').date()
                        months_left = (deadline.year - date.today().year) * 12 + (deadline.month - date.today().month)
                        if months_left > 0:
                            st.info(f"💡 Save **{curr} {shortfall / months_left:,.2f} / month**")
    else:
        st.info("No Sinking Funds created yet.")

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Future Payments")
    
    with st.expander("➕ Add New Schedule", expanded=True):
        with st.form("sch_form"):
            s_desc = st.text_input("Description (e.g. Rent, Investment)")
            c1, c2, c3 = st.columns(3)
            s_amount = c1.number_input("Amount", min_value=0.01)
            s_freq = c2.selectbox("Frequency", ["Monthly", "One-Time"])
            s_date = c3.date_input("Start Date", datetime.today())
            
            s_cat = st.selectbox("Category", get_categories())
            s_manual = st.checkbox("🔔 Manual Reminder? (Tick if you do the transfer manually)", value=False)
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense": s_from = st.selectbox("From Account", non_loan_accounts)
            elif s_type == "Income": s_to = st.selectbox("To Account", account_list)
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f")
                s_to = st.selectbox("To", account_list, key="s_t")
                
            if st.form_submit_button("Schedule It"):
                f_id = account_map.get(s_from)
                t_id = account_map.get(s_to)
                supabase.table('schedule').insert({
                    "description": s_desc, "amount": s_amount, "type": s_type, 
                    "from_account_id": f_id, "to_account_id": t_id, 
                    "frequency": s_freq, "next_run_date": str(s_date),
                    "is_manual": s_manual, "category": s_cat
                }).execute()
                st.success("Scheduled!")
                st.rerun()

    st.divider()
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.write("### 🗓️ Upcoming Items")
        df_up = pd.DataFrame(upcoming)
        df_up['Manual?'] = df_up['is_manual'].apply(lambda x: "🔔 Manual" if x else "🤖 Auto")
        if 'category' not in df_up.columns: df_up['category'] = "-"
        st.dataframe(df_up[['next_run_date', 'description', 'category', 'amount', 'frequency', 'Manual?', 'id']], hide_index=True)
        
        del_sch_id = st.number_input("Schedule ID to Delete", min_value=0)
        if st.button("Delete Schedule Item"):
            supabase.table('schedule').delete().eq("id", del_sch_id).execute()
            st.rerun()

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Configuration")
    
    with st.expander("📂 Manage Categories (Single)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Add Category**")
            new_cat = st.text_input("Name", key="new_cat_name")
            new_cat_type = st.selectbox("Type", ["Expense", "Income"], key="new_cat_type")
            if st.button("Add"):
                supabase.table('categories').insert({"name": new_cat, "type": new_cat_type}).execute()
                clear_cache() 
                st.success(f"Added {new_cat}")
                st.rerun()
        with c2:
            st.write("**Delete Category**")
            all_cats = get_categories()
            if all_cats:
                del_cat = st.selectbox("Select to Delete", all_cats)
                if st.button("Delete"):
                    supabase.table('categories').delete().eq("name", del_cat).execute()
                    clear_cache()
                    st.rerun()

    st.divider()

    with st.expander("➕ Add New Account (Single)", expanded=False):
        with st.form("create_acc"):
            new_name = st.text_input("Name")
            new_type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund", "Cash", "Loan", "Investment"])
            
            c_curr, c_rate = st.columns(2)
            new_curr = c_curr.selectbox("Currency", ["SGD", "USD", "RM", "CNY", "EUR", "GBP"])
            new_rate = c_rate.number_input("Exchange Rate (to SGD)", value=1.00, help="e.g. For RM, put 0.30")
            
            bal_label = "Starting Balance"
            if new_type == "Sinking Fund": bal_label = "Existing Saved Amount (Import from Excel)"
            elif new_type == "Loan": bal_label = "Current Loan Amount (Negative Value)"
            
            initial_bal = st.number_input(bal_label, value=0.0)
            new_remark = st.text_area("Account Notes (e.g., Min balance, Interest rate)", height=68)
            
            st.write("--- Goal Settings (Sinking Funds Only) ---")
            new_goal = st.number_input("Goal Amount", value=0.0)
            new_date = st.date_input("Goal Deadline", value=None)
            
            if st.form_submit_button("Create Account"):
                data = {
                    "name": new_name, "type": new_type, "balance": initial_bal,
                    "include_net_worth": True, "is_liquid_asset": True if new_type in ['Bank', 'Cash', 'Investment'] else False,
                    "goal_amount": new_goal if new_type == "Sinking Fund" else 0,
                    "goal_date": str(new_date) if new_type == "Sinking Fund" else None,
                    "sort_order": 99,
                    "is_active": True,
                    "remark": new_remark,
                    "currency": new_curr,
                    "manual_exchange_rate": new_rate
                }
                supabase.table('accounts').insert(data).execute()
                clear_cache()
                st.success(f"Created {new_name}!")
                st.rerun()

    st.divider()
    
    # EDIT ACCOUNT
    df_all_accounts = get_accounts(show_inactive=True)
    edit_list = df_all_accounts['name'].tolist() if not df_all_accounts.empty else []
    
    edit_acc = st.selectbox("Edit Existing Account", edit_list)
    if edit_acc:
        row = df_all_accounts[df_all_accounts['name'] == edit_acc].iloc[0]
        
        with st.form("edit_settings"):
            st.write(f"Editing: **{row['name']}**")
            
            c_name, c_sort = st.columns([3, 1])
            upd_name = c_name.text_input("Account Name", value=row['name'])
            upd_sort = c_sort.number_input("Sort Order", value=int(row.get('sort_order', 99)), step=1)
            
            # CURRENCY EDIT
            c_ec, c_er = st.columns(2)
            upd_curr = c_ec.selectbox("Currency", ["SGD", "USD", "RM", "CNY", "EUR", "GBP"], index=["SGD", "USD", "RM", "CNY", "EUR", "GBP"].index(row.get('currency', 'SGD')))
            upd_rate = c_er.number_input("Exchange Rate (to SGD)", value=float(row.get('manual_exchange_rate', 1.0)))

            upd_bal = st.number_input("Current Balance (Manual Adjustment)", value=float(row['balance']))
            upd_remark = st.text_area("Account Notes", value=row.get('remark', ''))

            c1, c2, c3 = st.columns(3)
            inc_nw = c1.checkbox("Include in Net Worth?", value=row['include_net_worth'])
            is_liq = c2.checkbox("Is Actual Bank Asset?", value=row['is_liquid_asset'])
            is_active = c3.checkbox("Account is Active?", value=row.get('is_active', True))
            
            g_amt = 0.0
            g_date = None
            if row['type'] == 'Sinking Fund':
                st.divider()
                st.write("Goal Settings")
                g_amt = st.number_input("Goal Amount", value=float(row['goal_amount'] or 0))
                g_date = st.date_input("Goal Deadline", value=datetime.strptime(row['goal_date'], '%Y-%m-%d') if row['goal_date'] else None)
            
            if st.form_submit_button("Save Changes"):
                update_account_settings(row['id'], upd_name, upd_bal, inc_nw, is_liq, g_amt, g_date, upd_sort, is_active, upd_remark, upd_curr, upd_rate)
                st.success("Updated! Refreshing...")
                st.rerun()
                
    st.divider()
    
    # --- BULK UPLOAD SECTION (SIMPLIFIED) ---
    with st.expander("📂 Bulk Import (Excel/CSV)", expanded=True):
        st.write("Upload a CSV file to import Accounts or Categories in bulk.")
        
        import_type = st.radio("What are you importing?", ["Accounts", "Categories"], horizontal=True)
        
        # 1. TEMPLATES
        if import_type == "Accounts":
            st.info("Required Columns: `name`, `type`, `balance` | Optional: `sort_order`, `remark`")
            sample_data = pd.DataFrame([
                {"name": "DBS Multiplier", "type": "Bank", "balance": 1000.0, "sort_order": 1, "remark": "Main"},
                {"name": "CIMB FastSaver", "type": "Bank", "balance": 500.0, "sort_order": 2, "remark": "Savings"},
                {"name": "Credit Card", "type": "Credit Card", "balance": -250.50, "sort_order": 3, "remark": "Pending"},
            ])
            st.download_button("Download Template CSV", sample_data.to_csv(index=False).encode('utf-8'), "accounts_template.csv", "text/csv")
        else:
            st.info("Required Columns: `name`, `type` (Expense/Income)")
            sample_data = pd.DataFrame([{"name": "Groceries", "type": "Expense"}, {"name": "Salary", "type": "Income"}])
            st.download_button("Download Template CSV", sample_data.to_csv(index=False).encode('utf-8'), "categories_template.csv", "text/csv")

        # 2. UPLOAD & PREVIEW
        uploaded_file = st.file_uploader("Upload CSV", type=['csv'])
        
        if uploaded_file:
            try:
                df_upload = pd.read_csv(uploaded_file)
                df_upload.columns = df_upload.columns.str.lower().str.strip()
                
                if import_type == "Accounts":
                    required_cols = ['name', 'type', 'balance']
                    # Add defaults if missing
                    defaults = {
                        'include_net_worth': True, 'is_liquid_asset': True, 
                        'sort_order': 99, 'is_active': True, 'remark': "", 
                        'currency': "SGD", 'manual_exchange_rate': 1.0, # Auto-default to SGD
                        'goal_amount': 0, 'goal_date': None
                    }
                    for col, val in defaults.items():
                        if col not in df_upload.columns: df_upload[col] = val
                        
                else:
                    required_cols = ['name', 'type']

                # VALIDATION
                missing = [c for c in required_cols if c not in df_upload.columns]
                if missing:
                    st.error(f"❌ Your CSV is missing these columns: {missing}")
                else:
                    st.write("### 👀 Data Preview")
                    st.dataframe(df_upload)
                    
                    if st.button(f"Confirm Import {len(df_upload)} Rows"):
                        # Clean NaN values
                        df_upload = df_upload.where(pd.notnull(df_upload), None)
                        records = df_upload.to_dict('records')
                        
                        target_table = 'accounts' if import_type == "Accounts" else 'categories'
                        supabase.table(target_table).insert(records).execute()
                        
                        clear_cache()
                        st.success("✅ Import Successful!")
                        
            except Exception as e:
                st.error(f"Error reading file: {e}")
