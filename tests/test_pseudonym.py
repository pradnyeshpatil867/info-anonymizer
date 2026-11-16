from app.identity.pseudonym import PseudonymGenerator, taken_checker

def never_taken(first, last):
    return False


def test_gender_match():
    gen = PseudonymGenerator(seed=1)
    # Faker's own list of female first names for the en_US locale.
    female_names = gen.fake.provider("faker.providers.person").first_names_female
    for _ in range(20):
        p = gen.generate("F", False, False, never_taken)
        assert p.first_name in female_names


def test_middle_name_only_when_real_person_has_one():
    gen = PseudonymGenerator(seed=2)
    assert gen.generate("M", has_middle_name=False, has_birth_name=False, is_taken=never_taken).middle_name is None
    assert gen.generate("M", has_middle_name=True, has_birth_name=False, is_taken=never_taken).middle_name


def test_middle_name_switched_off_by_config():
    gen = PseudonymGenerator(seed=3, include_middle_name=False)
    assert gen.generate("M", True, False, never_taken).middle_name is None


def test_birth_name_differs_from_last_name():
    gen = PseudonymGenerator(seed=4)
    for _ in range(20):
        p = gen.generate(None, False, True, never_taken)
        assert p.birth_name and p.birth_name != p.last_name


def test_taken_names_are_skipped():
    gen = PseudonymGenerator(seed=5)
    first_try = PseudonymGenerator(seed=5).generate("M", False, False, never_taken)
    is_taken = taken_checker([("pseudonym", first_try.first_name, first_try.last_name)])
    p = gen.generate("M", False, False, is_taken)
    assert (p.first_name, p.last_name) != (first_try.first_name, first_try.last_name)


def test_taken_checker_ignores_case_and_umlaut_spelling():
    is_taken = taken_checker([("real", "Jonas", "Müller")])
    assert is_taken("JONAS", "Mueller")
    assert not is_taken("Jonas", "Schmidt")
