#!/usr/bin/env python3
"""
Test suite for the AnkiDeckCleaner with exact string comparisons
"""

from anki_deck_cleaner import CardCleaner

def test_examples():
    cleaner = CardCleaner()
    
    # Test cases with expected outputs
    test_cases = [
        {
            "name": "Att försaka - multiple definitions with t.ex. in parentheses",
            "input": {
                "front": "Att försaka",
                "back": '1. Vara utan<br>(t.ex. "Vi fick försaka en hel del när vi köpte huset")<br><br>2. To renounce, to forsake, to give up<br>(t.ex. "Hon beslöt att försaka sitt arv."<br>"De försaker alla världsliga ting."<br>"Du har försakat familjen."<br><br>(syn: strunta i)'
            },
            "expected": {
                "front": "Att försaka (2)",
                "back": '1. Vara utan<br><span style="color: rgb(194, 194, 194)">"Vi fick <i>försaka</i> en hel del när vi köpte huset"</span><br><br>2. To renounce, to forsake, to give up<br><span style="color: rgb(194, 194, 194)">"Hon beslöt att <i>försaka</i> sitt arv."</span><br><span style="color: rgb(194, 194, 194)">"De <i>försaker</i> alla världsliga ting."</span><br><span style="color: rgb(194, 194, 194)">"Du har <i>försakat</i> familjen."</span><br><br><span style="color: rgb(194, 194, 194)">(syn: strunta i)</span>'
            }
        },
        {
            "name": "En stubin - already correct",
            "input": {
                "front": "En stubin",
                "back": 'A fuse<br><span style="color: #C2C2C2">"Alex hade kort <i>stubin</i>, ett brustet hjärta, och en ladda pistol."<br>(på stubinen: omedelbart)<br>(syn: stubintråd)</span>'
            },
            "expected": {
                "front": "En stubin",
                "back": 'A fuse<br><span style="color: #C2C2C2">"Alex hade kort <i>stubin</i>, ett brustet hjärta, och en ladda pistol."<br>(på stubinen: omedelbart)<br>(syn: stubintråd)</span>'
            }
        },
        {
            "name": "En stam - multiple Or, definitions with t.ex. inside span",
            "input": {
                "front": "En stam",
                "back": 'Trunk (of a tree)<br><span style="color: #C2C2C2">t.ex. "Trädet hade en tjock <i>stam</i>."</span><br>Or, Tribe<br><span style="color: #C2C2C2">"En stam av nomader reste genom öknen.", "Den Svenska Björnstammen"</span><br>Or, Del av ord, där böjningsaffix tagits bort<br><span style="color: #C2C2C2">(ordstam, rot)</span><br>Or, Strain (of bacteria, virus)<br><span style="color: #C2C2C2">"Forskarna studerade en ny stam av viruset."<br><br>(best: stammen, pl: stammar)</span>'
            },
            "expected": {
                "front": "En stam (4)",
                "back": '1. Trunk (of a tree)<br><span style="color: #C2C2C2">"Trädet hade en tjock <i>stam</i>."</span><br><br>2. Tribe<br><span style="color: #C2C2C2">"En <i>stam</i> av nomader reste genom öknen."<br>"Den Svenska Björnstammen"</span><br><br>3. Del av ord, där böjningsaffix tagits bort<br><span style="color: #C2C2C2">(ordstam, rot)</span><br><br>4. Strain (of bacteria, virus)<br><span style="color: #C2C2C2">"Forskarna studerade en ny <i>stam</i> av viruset."</span><br><br><span style="color: #C2C2C2">(best: <i>stammen</i>, pl: <i>stammar</i>)</span>'
            }
        },
        {
            "name": "En själ - with t.ex. in parentheses",
            "input": {
                "front": "En själ [sound:pronunciation_sv_själ.mp3]",
                "back": 'A soul (t.ex. "Kärnan i människans <i>själ</i> föds ur nya upplevelser.")<br>(en säl: a seal)'
            },
            "expected": {
                "front": "En själ [sound:pronunciation_sv_själ.mp3]",
                "back": 'A soul<br><span style="color: rgb(194, 194, 194)">"Kärnan i människans <i>själ</i> föds ur nya upplevelser."<br>(en säl: a seal)</span>'
            }
        },
        {
            "name": "Test with nbsp and gt entities",
            "input": {
                "front": "Test card",
                "back": 'Definition&nbsp;here&nbsp;&nbsp;&gt; more text<br>"Example&nbsp;sentence."<br>(syn: word)'
            },
            "expected": {
                "front": "Test card",
                "back": 'Definition here  > more text<br><span style="color: rgb(194, 194, 194)">"Example sentence."</span><br><span style="color: rgb(194, 194, 194)">(syn: word)</span>'
            }
        },
        {
            "name": "Att glida - do not italicize English glide",
            "input": {
                "front": "Att glida",
                "back": 'To slide / glide<br>"Jag gled på isen."'
            },
            "expected": {
                "front": "Att glida",
                "back": 'To slide / glide<br><span style="color: rgb(194, 194, 194)">"Jag gled på isen."</span>'
            }
        },
        {
            "name": "Belåten - keep usage note inside gray span",
            "input": {
                "front": "Belåten",
                "back": 'Content / pleased<br>"självbelåten": smug<br>Ordet används främst i uttryck såsom "nöjd och belåten" och "mätt och belåten".'
            },
            "expected": {
                "front": "Belåten",
                "back": 'Content / pleased<br><span style="color: rgb(194, 194, 194)">"självbelåten": smug<br>Ordet används främst i uttryck såsom "nöjd och <i>belåten</i>" och "mätt och <i>belåten</i>".</span>'
            }
        },
        {
            "name": "För övrigt - parenthesized quote group",
            "input": {
                "front": "För övrigt",
                "back": 'Furthermore / also (i förbi\u00ADgående sagt) ("Landet bör <i>för övrigt </i>stärka skyddet för dess minoritetsbefolkningar.",<br>"Liknande skillnader kan <i>för övrigt</i> observeras även för andra avfallstyper")'
            },
            "expected": {
                "front": "För övrigt",
                "back": 'Furthermore / also (i förbi gående sagt)<br><span style="color: rgb(194, 194, 194)">"Landet bör <i>för övrigt </i>stärka skyddet för dess minoritetsbefolkningar."<br>"Liknande skillnader kan <i>för övrigt</i> observeras även för andra avfallstyper"</span>'
            }
        },
        {
            "name": "Att bölja - do not italicize noun usage",
            "input": {
                "front": "Att bölja",
                "back": 'To billow<br>"En bölja reste sig."<br>"Vågorna började bölja."'
            },
            "expected": {
                "front": "Att bölja",
                "back": 'To billow<br><span style="color: rgb(194, 194, 194)">"En bölja reste sig."</span><br><span style="color: rgb(194, 194, 194)">"Vågorna började <i>bölja</i>."</span>'
            }
        },
        {
            "name": "Test with rgb color conversion",
            "input": {
                "front": "RGB test",
                "back": 'Main definition<br><span style="color: rgb(194, 194, 194);">"Example sentence"</span>'
            },
            "expected": {
                "front": "RGB test",
                "back": 'Main definition<br><span style="color: rgb(194, 194, 194);">"Example sentence"</span>'
            }
        },
        {
            "name": "Already correct card - should not change",
            "input": {
                "front": "Utan skor",
                "back": '<br><span style="color: #C2C2C2">"Han gick till jobbet <i>i strumplästen</i>."<br><br>(en läst: a shoe mold)</span>'
            },
            "expected": {
                "front": "Utan skor",
                "back": '<br><span style="color: #C2C2C2">"Han gick till jobbet <i>i strumplästen</i>."<br><br>(en läst: a shoe mold)</span>'
            }
        }
    ]
    
    print("Testing Card Cleaner with exact string comparisons\n" + "="*60)
    
    all_passed = True
    
    for i, test in enumerate(test_cases, 1):
        print(f"\nTest {i}: {test['name']}")
        print("-" * 60)
        
        # Run the cleaner
        new_front, new_back, changed = cleaner.clean_card(
            test['input']['front'], 
            test['input']['back']
        )
        
        # Check front field
        front_match = new_front == test['expected']['front']
        if not front_match:
            print("❌ Front field mismatch!")
            print(f"   Expected: {test['expected']['front']}")
            print(f"   Got:      {new_front}")
            all_passed = False
        else:
            print("✅ Front field matches")
        
        # Check back field
        back_match = new_back == test['expected']['back']
        if not back_match:
            print("❌ Back field mismatch!")
            print(f"   Expected: {test['expected']['back']}")
            print(f"   Got:      {new_back}")
            all_passed = False
        else:
            print("✅ Back field matches")
        
        # Check changed flag
        expected_changed = (test['input']['front'] != test['expected']['front'] or 
                           test['input']['back'] != test['expected']['back'])
        if changed != expected_changed:
            print("❌ Changed flag mismatch!")
            print(f"   Expected: {expected_changed}")
            print(f"   Got:      {changed}")
            all_passed = False
        else:
            print("✅ Changed flag correct ({changed})".format(changed=changed))
    
    print("\n" + "="*60)
    if all_passed:
        print("🎉 All tests passed!")
    else:
        print("❌ Some tests failed!")
    
    return all_passed

if __name__ == '__main__':
    success = test_examples()
    exit(0 if success else 1)
