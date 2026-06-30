import random
def main():
    print("welcome to number guessing game\n")
    choices=menu()
    comp_no=get_num(choices['range'])
    play_game(comp_no,choices['difficulty'])

def get_value(y):
    while True:
        try:
            x=int(input(f"enter {y} level :"))
            if x not in [1,2,3]:
                print("choose between 1 to 3")
            else:
                break
        except ValueError:
            print("choose between 1 to 3")
    return x
def menu():
    choices={}
    print("choose range :")
    print("pick 1 for Easy (Number betwenn 1-50)")
    print("pick 2 for Medium (Number betwenn 1-100)")
    print("pick 3 for Hard (Number between 1-150)")
    choices['range']=get_value("range")
    print("choose Difficulty :")
    print("pick 1 for Easy (10 Attempts)")
    print("pick 2 for Medium (7 Attempts)")
    print("pick 3 for Hard (5 Attempts)")
    choices['difficulty']=get_value("difficulty")
    return choices

def get_num(w):
    if w==1:
        z=random.randint(1,50)
    elif w==2:
        z=random.randint(1,100)
    elif w==3:
        z=random.randint(1,150)
    return z

def play_game(comp,y):
    apts={1:10,2:7,3:5}
    used_apt=0
    total_apt=apts[y]
    remain_apt=apts[y]
    while used_apt<total_apt:
        try:
            guess=int(input("enter your guess :"))
            if comp==guess:
                print(f"you guessed correctly , comp chose : {comp}")
                return
            else:
                mech(comp,guess)
            used_apt+=1
            remain_apt-=1
            print(f"used attempt : {used_apt} , remaining attempt : {remain_apt}")
        except ValueError:
            print("choose correct choice")
    print(f"you lose comp chose : {comp}")
def mech(comp,guess):
    if comp-guess>=20:
          print("too low(go higher)")
    elif guess-comp>=20:
          print("too high(go lower)")
    elif comp-guess>=10 and comp-guess<20:
          print("low(go higher)")
    elif guess-comp>=10 and guess-comp<20:
          print("high(go lower)")
    elif comp-guess<10 and comp-guess>0:
        print("very close(go higher)")
    elif guess-comp<10 and guess-comp>0:
        print("very close(go lower)")

if __name__ == "__main__":
    main()