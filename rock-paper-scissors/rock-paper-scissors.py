import random
choices= ['rock' , 'paper' , 'scissors']
wins= {1:2,2:0,3:1}
def play_game():     
    comp_score=0
    user_score=0   
    while True:
        print("choose:\n0 for exit\n1 for rock\n2 for paper\n3 for scissors\n4 for score")
        try:
            user_choice=int(input("Enter Your Choice : "))
            if user_choice==0:
                print(f"Final score - You: {user_score}, Computer: {comp_score}")
                break

            elif user_choice==4:
                print(f"Your score : {user_score} , Comp score : {comp_score}")
            elif user_choice>0 and user_choice<4:
                comp_choice=random.randint(0,2)
                if comp_choice==(user_choice-1):
                    print(f"tie , comp chose : {choices[comp_choice]}")
                elif wins[user_choice]==comp_choice:
                    print(f"you won , comp chose : {choices[comp_choice]}")
                    user_score+=1
                else:
                    print(f"you lose , comp chose : {choices[comp_choice]}")
                    comp_score+=1
            else:
                print("enter choice between 0 and 4")
        except ValueError:
            print("enter valid choice :")
def main():   
    print("welcome to the game")
    play_game()
if __name__ == '__main__':
    main()