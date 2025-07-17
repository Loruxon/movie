import React, { useState, useEffect, useRef } from 'react';

// Универсальный выпадающий список
const Dropdown = ({
  options = [],
  selected,
  onSelect,
  placeholder = 'Выберите...',
  isLazy = false,
  onSearch,
  searchTerm = '',
  hasMore = false,
  loadMore,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const closeOnOutsideClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
        onSearch?.('');
      }
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, []);

  const selectedOption = options.find((o) => o.id === selected);
  const filtered = options.filter((opt) =>
    opt.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-3 py-2 bg-slate-700 hover:bg-slate-600 text-left text-white rounded-md flex justify-between items-center"
      >
        <span>{selectedOption?.name || placeholder}</span>
        <svg className={`w-4 h-4 transform ${open ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-slate-800 border border-slate-600 rounded-md shadow-lg">
          {isLazy && (
            <div className="p-2">
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => onSearch?.(e.target.value)}
                placeholder="Поиск..."
                className="w-full px-3 py-2 rounded-md bg-slate-900 text-white placeholder-slate-500 focus:outline-none"
              />
            </div>
          )}
          <ul
            className="max-h-60 overflow-y-auto text-sm"
            onScroll={(e) => {
              const { scrollTop, scrollHeight, clientHeight } = e.target;
              if (scrollHeight - scrollTop <= clientHeight + 5 && hasMore) loadMore?.();
            }}
          >
            {filtered.length ? (
              filtered.map((opt) => (
                <li
                  key={opt.id}
                  onClick={() => {
                    onSelect(opt.id);
                    setOpen(false);
                    onSearch?.('');
                  }}
                  className={`px-4 py-2 cursor-pointer hover:bg-slate-700 ${
                    selected === opt.id ? 'bg-slate-700' : ''
                  }`}
                >
                  {opt.name}
                </li>
              ))
            ) : (
              <li className="px-4 py-2 text-slate-400 italic">Нет результатов</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
};

// Новая шапка сайта
const Header = () => {
  return (
    <header className="flex items-center justify-between px-6 py-4 bg-slate-800 border-b border-slate-700">
      {/* Логотип */}
      <div className="flex items-center space-x-3 cursor-pointer select-none">
        <div className="text-3xl">🎬</div>
        <h1 className="text-xl font-bold text-white tracking-wide">Мой Кинотеатр</h1>
      </div>

      {/* Меню */}
      <nav>
        <ul className="flex space-x-6 text-slate-300 text-sm font-medium">
          <li>
            <a href="/" className="hover:text-white transition-colors duration-200">
              Главная
            </a>
          </li>
          <li>
            <a href="#movies" className="hover:text-white transition-colors duration-200">
              Фильмы
            </a>
          </li>
          <li>
            <a href="#genres" className="hover:text-white transition-colors duration-200">
              Жанры
            </a>
          </li>
          <li>
            <a href="#about" className="hover:text-white transition-colors duration-200">
              О проекте
            </a>
          </li>
          <li>
            <a href="#contacts" className="hover:text-white transition-colors duration-200">
              Контакты
            </a>
          </li>
        </ul>
      </nav>
    </header>
  );
};

const App = () => {
  const [movies, setMovies] = useState([]);
  const [search, setSearch] = useState('');

  const [genres, setGenres] = useState([]);
  const [countries, setCountries] = useState([]);
  const [actors, setActors] = useState([]);
  const [directors, setDirectors] = useState([]);

  const [selectedGenre, setSelectedGenre] = useState(null);
  const [selectedCountry, setSelectedCountry] = useState(null);
  const [selectedYear, setSelectedYear] = useState(null);
  const [selectedActor, setSelectedActor] = useState(null);
  const [selectedDirector, setSelectedDirector] = useState(null);

  const [actorSearch, setActorSearch] = useState('');
  const [directorSearch, setDirectorSearch] = useState('');

  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);

  const years = Array.from({ length: 2025 - 1990 + 1 }, (_, i) => {
    const year = 1990 + i;
    return { id: year, name: String(year) };
  }).reverse();

  const [genrePage, setGenrePage] = useState(1);
  const [countryPage, setCountryPage] = useState(1);
  const [actorPage, setActorPage] = useState(1);
  const [directorPage, setDirectorPage] = useState(1);

  const [hasMoreGenres, setHasMoreGenres] = useState(true);
  const [hasMoreCountries, setHasMoreCountries] = useState(true);
  const [hasMoreActors, setHasMoreActors] = useState(true);
  const [hasMoreDirectors, setHasMoreDirectors] = useState(true);

  const API = 'http://192.168.1.37:8000/api';

  const fetchData = (endpoint, setter, page = 1, append = false, mapFn = (i) => ({ id: i.id, name: i.name })) => {
    fetch(`${API}/${endpoint}?page=${page}`)
      .then((res) => res.json())
      .then((data) => {
        setter((prev) =>
          append ? [...prev, ...data.results.map(mapFn)] : data.results.map(mapFn)
        );
        if (endpoint.includes('genre')) {
          setHasMoreGenres(!!data.next);
          setGenrePage(page);
        } else if (endpoint.includes('country')) {
          setHasMoreCountries(!!data.next);
          setCountryPage(page);
        }
      });
  };

  const fetchPersons = (type, search, page = 1, append = false, setter, setPageState, setMoreState) => {
    if (search.length < 3) return;
    let url = `${API}/person/?search=${search}`;
    if (page > 1) url += `&page=${page}`;
    fetch(url)
      .then((res) => res.json())
      .then((data) => {
        setter((prev) =>
          append ? [...prev, ...data.results.map((p) => ({ id: p.id, name: p.name_ru }))] : data.results.map((p) => ({ id: p.id, name: p.name_ru }))
        );
        setMoreState(!!data.next);
        setPageState(page);
      });
  };

  const fetchMovies = (p = 1, append = false) => {
    const params = new URLSearchParams();
    if (search.trim()) params.append('search', search.trim());
    if (selectedGenre) params.append('genres', selectedGenre);
    if (selectedCountry) params.append('countries', selectedCountry);
    if (selectedYear) params.append('release_year', selectedYear);
    if (selectedActor) params.append('cast', selectedActor);
    if (selectedDirector) params.append('directors', selectedDirector);
    params.append('page', p);

    fetch(`${API}/movie/?${params.toString()}`)
      .then((res) => res.json())
      .then((data) => {
        setMovies((prev) => (append ? [...prev, ...data.results] : data.results));
        setHasMore(!!data.next);
        setPage(p);
      });
  };

  useEffect(() => {
    fetchData('genre', setGenres);
    fetchData('country', setCountries);
  }, []);

  useEffect(() => {
    fetchPersons('actor', actorSearch, 1, false, setActors, setActorPage, setHasMoreActors);
  }, [actorSearch]);

  useEffect(() => {
    fetchPersons('director', directorSearch, 1, false, setDirectors, setDirectorPage, setHasMoreDirectors);
  }, [directorSearch]);

  useEffect(() => {
    fetchMovies(1);
  }, [search, selectedGenre, selectedCountry, selectedYear, selectedActor, selectedDirector]);

  const reset = () => {
    setSearch('');
    setSelectedGenre(null);
    setSelectedCountry(null);
    setSelectedYear(null);
    setSelectedActor(null);
    setSelectedDirector(null);
    setActorSearch('');
    setDirectorSearch('');
    fetchMovies(1);
  };

  return (
    <div className="min-h-screen bg-slate-900 text-white text-sm flex flex-col">
      <Header />
      <div className="flex flex-1">
        <aside className="w-full md:w-64 p-4 border-r border-slate-700 bg-slate-800">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск фильмов..."
            className="mb-4 w-full px-3 py-2 rounded-md bg-slate-700 text-white placeholder-slate-400 focus:outline-none"
          />
          <div className="space-y-4">
            <Dropdown
              options={genres}
              selected={selectedGenre}
              onSelect={setSelectedGenre}
              placeholder="Жанр"
              hasMore={hasMoreGenres}
              loadMore={() => fetchData('genre', setGenres, genrePage + 1, true)}
            />
            <Dropdown
              options={countries}
              selected={selectedCountry}
              onSelect={setSelectedCountry}
              placeholder="Страна"
              hasMore={hasMoreCountries}
              loadMore={() => fetchData('country', setCountries, countryPage + 1, true)}
            />
            <Dropdown
              options={years}
              selected={selectedYear}
              onSelect={setSelectedYear}
              placeholder="Год выпуска"
            />
            <Dropdown
              options={actors}
              selected={selectedActor}
              onSelect={setSelectedActor}
              placeholder="Актёр"
              isLazy
              searchTerm={actorSearch}
              onSearch={setActorSearch}
              hasMore={hasMoreActors}
              loadMore={() =>
                fetchPersons('actor', actorSearch, actorPage + 1, true, setActors, setActorPage, setHasMoreActors)
              }
            />
            <Dropdown
              options={directors}
              selected={selectedDirector}
              onSelect={setSelectedDirector}
              placeholder="Режиссёр"
              isLazy
              searchTerm={directorSearch}
              onSearch={setDirectorSearch}
              hasMore={hasMoreDirectors}
              loadMore={() =>
                fetchPersons('director', directorSearch, directorPage + 1, true, setDirectors, setDirectorPage, setHasMoreDirectors)
              }
            />
            <button onClick={reset} className="w-full px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-md">
              Сбросить фильтры
            </button>
          </div>
        </aside>
        <main className="flex-1 p-4">
          {movies.length === 0 ? (
            <div className="text-center text-slate-400">Фильмы не найдены</div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {movies.map((movie) => (
                <div
                  key={movie.id}
                  className="group relative bg-slate-800 rounded-md overflow-hidden shadow-md hover:shadow-lg transition"
                >
                  <img src={movie.poster} alt={movie.title_ru} className="w-full object-cover rounded-md" />
                  <div className="absolute top-2 right-2 bg-black/70 text-xs px-2 py-0.5 rounded">★ {Number(movie.rating).toFixed(1)}</div>
                  <div className="absolute bottom-0 left-0 right-0 px-3 py-2 bg-black/60 text-xs">
                    <div className="font-semibold truncate">{movie.title_ru}</div>
                    <div className="text-slate-300 truncate">{movie.release_year}, {movie.genres.map((g) => g.name).join(', ')}</div>
                  </div>
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 group-hover:opacity-100 transition">
                    <svg className="h-10 w-10 text-white" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </div>
                </div>
              ))}
            </div>
          )}
          {hasMore && (
            <div className="mt-6 text-center">
              <button
                onClick={() => fetchMovies(page + 1, true)}
                className="px-6 py-2 bg-slate-700 hover:bg-slate-600 rounded-md font-medium"
              >
                Смотреть ещё...
              </button>
            </div>
          )}
        </main>
      </div>
      <footer className="py-4 text-center text-slate-500 text-xs border-t border-slate-700">
        © 2025 Мой кинотеатр. Все права защищены.
      </footer>
    </div>
  );
};

export default App;