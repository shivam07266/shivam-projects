import random 
def main():
    print('Welcome to password generator\n')
#   choices={1:'upper_included',2:'lower_included', 3:'digits_included',4:'symbols_included'}
    length=get_length()
    val=list_of_choices()
    password=gen_ps(length,val)
    print(f"your password is : {password}")
    password_strength(password)

    
def get_length():
    while True:
        try:
            length=int(input("how many length do you want to have:"))
            if length>0:
                break
            else:
                print("choose a positive integer")
        except ValueError:
            print("choose a positive integer")
    return length
    

def get_choice(y):
    while True:
        choice=input(f"do you want to have {y} (y/n): ").lower()
        if choice not in ['y','n']:
            print("enter only y or n")
        else:
            break
    return choice

def list_of_choices():
    val={}
    tita=[]
    val['has_upper']=get_choice('uppercase')
    val['has_lower']=get_choice('lowercase')
    val['has_digits']=get_choice('digits')
    val['has_symbols']=get_choice('symbols') 
    for i in val:
        if val[i]=='y':
            tita.append((list(val.keys()).index(i))+1)
    if len(tita)==0:
        print("choose atleast 1 y")
        return list_of_choices()
    else:
        return tita

def gen_ps(length,val):
    password=''
    for i in range(length):
        r=random.choice(val)
        if r==1:
            j=random.randint(65,90)
            password+=chr(j)
        elif r==2:
            j=random.randint(97,122)
            password+=chr(j)
        elif r==3:
            j=random.randint(0,9)
            password+=str(j)
        elif r==4:
            j=random.randint(33,47)
            password+=chr(j)
    return password
def issymbol(x):
    if ord(x)>32 and ord(x)<48:
        return True

def password_strength(password):
    score = 0


    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if any(char.isupper() for char in password):
        score += 1
    if any(char.islower() for char in password):
        score += 1
    if any(char.isdigit() for char in password):
        score += 1
    if any(issymbol(char) for char in password):
        score += 1
    status=''
    if score<3:
       status='weak'
    elif score<5 and score>2:
       status='mid' 
    elif score>5:
        status='strong'
    print(f"password strength is : {status}") 
main()
    
